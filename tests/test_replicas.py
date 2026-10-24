"""Two replicas over one Postgres, as the `scale` profile runs them, each test on a fresh database.
Skipped without TEST_DATABASE_URL (CI has one); sockets open to the local server only."""

import asyncio
import json
import os
from collections import Counter
from contextlib import AsyncExitStack, suppress
from uuid import uuid4

import httpx
import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from app.contracts import Intent, TargetAgent
from app.graph.build import build_graph
from app.graph.human_review import HandoffDraft, OperatorReply
from app.graph.router import PlanStep, RouterDecision
from app.graph.support import SupportAction, SupportAnswer, SupportPlan
from app.main import create_app
from app.rag.retrieve import HybridRetriever
from app.settings import Settings
from app.storage import open_storage
from tests.identity import IDENTITY, serve

SERVER = os.environ.get("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.skipif(not SERVER, reason="needs TEST_DATABASE_URL, a Postgres server"),
    pytest.mark.enable_socket,
    pytest.mark.allow_hosts(["127.0.0.1", "::1"]),
]

OPERATOR, MERCHANT = IDENTITY.operator(), IDENTITY.merchant("cliente1988")
COMPLAINT = "Por que o estorno da minha venda ainda não apareceu?"
QUESTION = "Quando cai o dinheiro das vendas de ontem?"
SETTLED = "O dinheiro das vendas de ontem, R$ 231,26 líquidos, cai hoje."
OPERATOR_SAYS = "O estorno foi aprovado hoje e volta ao comprador em até 2 dias úteis."
LOOKUP = SupportPlan(action=SupportAction.CALL_TOOL, tool="get_account_profile")
REFUND = SupportPlan(action=SupportAction.ESCALATE_INDIVIDUAL_CASE)


class Model:
    """One vendor for both replicas: a refund escalates, anything else reads the account. With
    `hang`, the first account answer never comes back; with `overlap`, each router call waits up
    to half a second for a second turn to be routing too."""

    def __init__(self, hang: asyncio.Event | None = None, overlap: bool = False) -> None:
        self.calls, self.hang, self.overlap = Counter(), hang, overlap
        self.routing = self.most = 0
        self.second = asyncio.Event()

    async def structured(self, *, system: str, user: str, schema: type):
        self.calls[schema.__name__] += 1
        if schema is RouterDecision and self.overlap:
            self.routing += 1
            self.most = max(self.most, self.routing)
            if self.routing > 1:
                self.second.set()
            with suppress(TimeoutError):
                await asyncio.wait_for(self.second.wait(), 0.5)
            self.routing -= 1
        if schema is SupportAnswer and self.hang and self.calls["SupportAnswer"] == 1:
            self.hang.set()
            await asyncio.sleep(3600)
        if schema is RouterDecision:
            step = PlanStep(agent=TargetAgent.SUPPORT, query_pt=json.loads(user)["message"])
            decision = {"intent": Intent.ACCOUNT_DATA, "confidence": 0.9, "rationale": "t"}
            return RouterDecision(**decision, steps=[step])
        return {
            SupportPlan: REFUND if "estorno" in user else LOOKUP,
            SupportAnswer: SupportAnswer(answer=SETTLED),
            HandoffDraft: HandoffDraft(draft="?"),
            OperatorReply: OperatorReply(answer=OPERATOR_SAYS),
        }[schema]


@pytest.fixture
async def replicas(sample_chunks, fake_collection):
    """Replicas over a fresh database on the test server, each with its own pools, graph and app."""
    name = f"t_{uuid4().hex[:12]}"
    async with await psycopg.AsyncConnection.connect(SERVER, autocommit=True) as admin:
        await admin.execute(f'CREATE DATABASE "{name}"')
    settings = Settings(storage="postgres", database_url=make_conninfo(SERVER, dbname=name))

    async def start(exits: AsyncExitStack, model: Model) -> list[httpx.AsyncClient]:
        clients = []
        for _ in range(2):
            storage = await exits.enter_async_context(open_storage(settings))
            retriever = HybridRetriever(sample_chunks, fake_collection(), top_k=5)
            graph = build_graph(model, settings, retriever, storage.checkpointer, storage.queue)
            api = create_app()
            serve(api, settings, storage, graph, retriever)
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://r")
            clients.append(await exits.enter_async_context(client))
        return clients

    yield start
    async with await psycopg.AsyncConnection.connect(SERVER, autocommit=True) as admin:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


async def chat(http, message: str) -> dict:
    payload = {"message": message, "conversation_id": "c1"}
    response = await http.post("/chat", json=payload, headers=MERCHANT)
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_case_escalated_on_one_replica_is_answered_on_the_other(replicas):
    async with AsyncExitStack() as exits:
        a, b = await replicas(exits, Model())
        protocol = (await chat(a, COMPLAINT))["escalation"]["protocol"]
        listed = (await b.get("/handoffs", headers=OPERATOR)).json()
        decision = {"action": "reply", "answer": OPERATOR_SAYS}
        resumed = await b.post(f"/handoffs/{protocol}/resume", json=decision, headers=OPERATOR)
        after = await chat(a, QUESTION)

    assert [record["case"]["protocol"] for record in listed] == [protocol]
    assert resumed.status_code == 200
    assert [reply["answer"] for reply in after["delivered"]] == [OPERATOR_SAYS]
    assert after["answer"] == SETTLED


async def test_a_turn_whose_replica_dies_resumes_from_its_checkpoint_on_the_other(replicas):
    model = Model(hang=asyncio.Event())
    async with AsyncExitStack() as exits:
        a, b = await replicas(exits, model)
        dying = asyncio.create_task(chat(a, QUESTION))
        await asyncio.wait_for(model.hang.wait(), 5)
        dying.cancel()
        with suppress(asyncio.CancelledError):
            await dying
        retried = await chat(b, QUESTION)  # the merchant retries; the balancer picks B
        view = (await b.get("/conversations/c1", headers=MERCHANT)).json()

    assert retried["answer"] == SETTLED
    assert model.calls["RouterDecision"] == 1, "B resumed A's plan instead of routing again"
    assert [message["content"] for message in view["messages"]] == [QUESTION, SETTLED]


async def test_one_conversation_takes_turns_across_replicas(replicas):
    model = Model(overlap=True)
    async with AsyncExitStack() as exits:
        a, b = await replicas(exits, model)
        await asyncio.gather(chat(a, QUESTION), chat(b, "E o dinheiro das vendas de hoje?"))
        view = (await a.get("/conversations/c1", headers=MERCHANT)).json()

    assert model.most == 1
    assert len(view["messages"]) == 4, "neither turn was written over the other"
