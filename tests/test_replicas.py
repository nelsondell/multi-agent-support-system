"""Two replicas over one Postgres, as the compose `scale` profile runs them.

A case escalated on one replica is answered through the other and delivered by the first; a turn
whose replica dies resumes from its checkpoint on the other; turns on one conversation take turns
across replicas while other conversations run at once. Each test gets a database of its own.

Skipped unless TEST_DATABASE_URL names a Postgres server (CI starts one). For these tests the
suite's socket block is lifted, to the local server only.
"""

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

SERVER = os.environ.get("TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.skipif(not SERVER, reason="needs TEST_DATABASE_URL, a Postgres server"),
    pytest.mark.enable_socket,
    pytest.mark.allow_hosts(["127.0.0.1", "::1"]),
]

CALLER = "cliente1988"
TOKEN = "operator-test-token"
OPERATOR = {"Authorization": f"Bearer {TOKEN}"}
COMPLAINT = "Por que o estorno da minha venda ainda não apareceu?"
QUESTION = "Quando cai o dinheiro das vendas de ontem?"
SETTLED = "O dinheiro das vendas de ontem, R$ 231,26 líquidos, cai hoje."
OPERATOR_SAYS = "O estorno foi aprovado hoje e volta ao comprador em até 2 dias úteis."


def routed(user: str) -> RouterDecision:
    """A refund is an individual case; anything else reads the account."""
    message = json.loads(user)["message"]
    intent = Intent.INDIVIDUAL_CASE if "estorno" in message else Intent.ACCOUNT_DATA
    steps = [PlanStep(agent=TargetAgent.SUPPORT, query_pt=message)]
    return RouterDecision(intent=intent, confidence=0.9, rationale="t", steps=steps)


def planned(user: str) -> SupportPlan:
    if "estorno" in user:
        return SupportPlan(action=SupportAction.ESCALATE_INDIVIDUAL_CASE)
    return SupportPlan(
        action=SupportAction.CALL_TOOL,
        tool="get_settlement_schedule",
        date_from="2026-08-31",
        date_to="2026-08-31",
    )


class Model:
    """One model shared by both replicas, as one vendor is; answers may be async functions."""

    def __init__(self, **overrides) -> None:
        answers = {
            RouterDecision: routed,
            SupportPlan: planned,
            SupportAnswer: SupportAnswer(answer=SETTLED),
            HandoffDraft: HandoffDraft(draft="Estamos verificando o estorno."),
            OperatorReply: lambda user: OperatorReply(answer=json.loads(user)["operator_answer"]),
        }
        by_name = {schema.__name__: schema for schema in answers}
        self.answers = {**answers, **{by_name[name]: value for name, value in overrides.items()}}
        self.calls: Counter = Counter()

    async def structured(self, *, system: str, user: str, schema: type):
        self.calls[schema.__name__] += 1
        answer = self.answers[schema]
        if asyncio.iscoroutinefunction(answer):
            return await answer(user)
        return answer(user) if callable(answer) else answer


@pytest.fixture
async def database():
    """A fresh database on the test server, dropped afterwards."""
    name = f"t_{uuid4().hex[:12]}"
    async with await psycopg.AsyncConnection.connect(SERVER, autocommit=True) as admin:
        await admin.execute(f'CREATE DATABASE "{name}"')
    yield make_conninfo(SERVER, dbname=name)
    async with await psycopg.AsyncConnection.connect(SERVER, autocommit=True) as admin:
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def replicas(database, sample_chunks, fake_collection):
    """Start replicas over the same database, each with its own pools, graph and app."""
    settings = Settings(storage="postgres", database_url=database, operator_token=TOKEN)

    async def start(exits: AsyncExitStack, model: Model, count: int = 2) -> list:
        clients = []
        for _ in range(count):
            storage = await exits.enter_async_context(open_storage(settings))
            similar = fake_collection({chunk.id: 0.9 for chunk in sample_chunks})
            retriever = HybridRetriever(sample_chunks, similar, top_k=5)
            api = create_app()
            api.state.settings, api.state.retriever = settings, retriever
            api.state.queue, api.state.locks = storage.queue, storage.locks
            api.state.graph = build_graph(
                model, settings, retriever, storage.checkpointer, storage.queue
            )
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://r")
            clients.append(await exits.enter_async_context(client))
        return clients

    return start


async def chat(http, message: str, conversation_id: str = "c1") -> dict:
    payload = {"message": message, "user_id": CALLER, "conversation_id": conversation_id}
    response = await http.post("/chat", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_case_escalated_on_one_replica_is_answered_on_the_other(replicas):
    async with AsyncExitStack() as exits:
        a, b = await replicas(exits, Model())
        paused = await chat(a, COMPLAINT)
        protocol = paused["escalation"]["protocol"]
        listed = (await b.get("/handoffs", headers=OPERATOR)).json()
        decision = {"action": "reply", "answer": OPERATOR_SAYS}
        resumed = await b.post(f"/handoffs/{protocol}/resume", json=decision, headers=OPERATOR)
        after = await chat(a, QUESTION)

    assert paused["status"] == "pending_human"
    assert [record["case"]["protocol"] for record in listed] == [protocol]
    assert resumed.status_code == 200
    assert [reply["answer"] for reply in after["delivered"]] == [OPERATOR_SAYS]
    assert after["answer"] == SETTLED


async def test_a_turn_whose_replica_dies_resumes_from_its_checkpoint_on_the_other(replicas):
    entered = asyncio.Event()

    async def answer(user: str) -> SupportAnswer:
        if model.calls["SupportAnswer"] == 1:  # replica A, which dies here
            entered.set()
            await asyncio.sleep(3600)
        return SupportAnswer(answer=SETTLED)

    model = Model(SupportAnswer=answer)
    async with AsyncExitStack() as exits:
        a, b = await replicas(exits, model)
        dying = asyncio.create_task(
            a.post("/chat", json={"message": QUESTION, "user_id": CALLER, "conversation_id": "c1"})
        )
        await asyncio.wait_for(entered.wait(), 5)
        dying.cancel()
        with suppress(asyncio.CancelledError):
            await dying
        retried = await chat(b, QUESTION)  # the merchant retries; the balancer picks B
        view = (await b.get("/conversations/c1", params={"user_id": CALLER})).json()

    assert retried["answer"] == SETTLED
    assert model.calls["RouterDecision"] == 1, "B resumed A's plan instead of routing again"
    assert [message["content"] for message in view["messages"]] == [QUESTION, SETTLED]


class Overlap(Model):
    """Holds each router call until a second turn is routing too, or half a second passes."""

    def __init__(self) -> None:
        super().__init__()
        self.routing = self.most = 0
        self.second = asyncio.Event()

    async def structured(self, *, system: str, user: str, schema: type):
        if schema is not RouterDecision:
            return await super().structured(system=system, user=user, schema=schema)
        self.routing += 1
        self.most = max(self.most, self.routing)
        if self.routing > 1:
            self.second.set()
        try:
            with suppress(TimeoutError):
                await asyncio.wait_for(self.second.wait(), 0.5)
            return await super().structured(system=system, user=user, schema=schema)
        finally:
            self.routing -= 1


async def test_one_conversation_takes_turns_across_replicas(replicas):
    model = Overlap()
    async with AsyncExitStack() as exits:
        a, b = await replicas(exits, model)
        await asyncio.gather(chat(a, QUESTION), chat(b, "E o dinheiro das vendas de hoje?"))
        view = (await a.get("/conversations/c1", params={"user_id": CALLER})).json()

    assert model.most == 1
    assert len(view["messages"]) == 4, "neither turn was written over the other"


async def test_two_conversations_run_at_once_across_replicas(replicas):
    model = Overlap()
    async with AsyncExitStack() as exits:
        a, b = await replicas(exits, model)
        await asyncio.gather(chat(a, QUESTION, "c1"), chat(b, QUESTION, "c2"))

    assert model.most == 2


async def test_replicas_booting_together_create_the_tables_once(database):
    settings = Settings(storage="postgres", database_url=database)
    async with AsyncExitStack() as exits:
        stores = await asyncio.gather(
            *(exits.enter_async_context(open_storage(settings)) for _ in range(2))
        )
        for storage in stores:
            await storage.queue.ping()
        ready = await stores[0].queue.list_open()

    assert ready == []


async def test_ready_reports_the_store_and_the_index(replicas):
    async with AsyncExitStack() as exits:
        (a,) = await replicas(exits, Model(), count=1)
        response = await a.get("/ready")

    assert response.status_code == 200
    assert response.json()["storage"] == "postgres"
    assert response.json()["chunks"] > 0
