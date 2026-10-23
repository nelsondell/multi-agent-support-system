"""The one write action: a technical ticket, proposed by the agent and run only on the merchant's
confirmation (Meta's Agents Rule of Two). cliente2042's terminal is offline in the mock data;
cliente1988's is online."""

import asyncio
import json
import re
import sqlite3
from contextlib import AsyncExitStack

import httpx
import pytest

from app.contracts import Intent, TargetAgent
from app.graph.build import build_graph
from app.graph.human_review import HandoffDraft
from app.graph.router import PlanStep, RouterDecision
from app.graph.support import SupportAction, SupportAnswer, SupportPlan
from app.main import create_app
from app.prompts import load_prompt
from app.rag.retrieve import HybridRetriever
from app.settings import Settings
from app.storage import open_storage
from tests.identity import IDENTITY, serve

OFFLINE, ONLINE = "cliente2042", "cliente1988"
STILL_DOWN = "Minha maquininha continua sem conexão mesmo depois de reiniciar e testar o chip."
SUPERVISOR = IDENTITY.operator("bia", "supervisor")


def routed(user: str) -> RouterDecision:
    message = json.loads(user)["message"]
    steps = [PlanStep(agent=TargetAgent.SUPPORT, query_pt=message)]
    return RouterDecision(intent=Intent.TROUBLESHOOTING, confidence=0.9, rationale="t", steps=steps)


def planned(user: str) -> SupportPlan:
    if "sem conexão" in user:
        return SupportPlan(
            action=SupportAction.PROPOSE_TECHNICAL_TICKET,
            symptom="sem conexão depois de reiniciar e testar o chip",
        )
    return SupportPlan(action=SupportAction.CALL_TOOL, tool="get_transactions")


class Model:
    answers = {
        RouterDecision: routed,
        SupportPlan: planned,
        SupportAnswer: SupportAnswer(answer="Suas vendas recentes estão em dia."),
        HandoffDraft: HandoffDraft(draft="Estamos verificando."),
    }

    async def structured(self, *, system: str, user: str, schema: type):
        answer = self.answers[schema]
        return answer(user) if callable(answer) else answer


@pytest.fixture
def service(tmp_path, sample_chunks, fake_collection):
    async def start(exits: AsyncExitStack):
        settings = Settings(state_dir=str(tmp_path / "state"))
        storage = await exits.enter_async_context(open_storage(settings))
        retriever = HybridRetriever(sample_chunks, fake_collection(), top_k=5)
        graph = build_graph(
            Model(), settings, retriever, storage.checkpointer, storage.queue, None, storage.desk
        )
        api = create_app()
        serve(api, settings, storage, graph, retriever)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://t")
        return await exits.enter_async_context(client), storage

    return start


async def chat(http, merchant: str, message: str, confirm: str | None = None):
    payload = {"message": message, "conversation_id": "c1"}
    if confirm:
        payload["confirm_action_id"] = confirm
    return await http.post("/chat", json=payload, headers=IDENTITY.merchant(merchant))


def tickets(tmp_path) -> list:
    with sqlite3.connect(tmp_path / "state" / "handoffs.sqlite") as connection:
        return connection.execute("SELECT id, user_id, terminal_id FROM tickets").fetchall()


async def test_a_proposal_waits_for_the_merchant_and_shows_the_operation(service, tmp_path):
    async with AsyncExitStack() as exits:
        http, _ = await service(exits)
        proposed = (await chat(http, OFFLINE, STILL_DOWN)).json()

    action = proposed["pending_action"]
    assert proposed["status"] == "pending_confirmation"
    assert re.fullmatch(r"[0-9a-f]{32}", action["id"])
    assert action["operation"] == (
        'open_technical_ticket(terminal_id="GC-1180-7742", '
        'symptom="sem conexão depois de reiniciar e testar o chip")'
    )
    assert action["operation"] in proposed["answer"] and action["id"] in proposed["answer"]
    assert tickets(tmp_path) == []


async def test_without_confirmation_nothing_is_written(service, tmp_path):
    async with AsyncExitStack() as exits:
        http, _ = await service(exits)
        await chat(http, OFFLINE, STILL_DOWN)
        moved_on = (await chat(http, OFFLINE, "Deixa pra lá. Como estão minhas vendas?")).json()
        audit = (await http.get("/audit", headers=SUPERVISOR)).json()

    assert moved_on["status"] == "answered" and moved_on["pending_action"] is None
    assert moved_on["answer"] == "Suas vendas recentes estão em dia."
    assert tickets(tmp_path) == []
    assert [entry["action"] for entry in audit][:2] == ["action.cancelled", "action.proposed"]


async def test_one_confirmation_writes_once_and_a_replay_returns_the_same_receipt(
    service, tmp_path
):
    async with AsyncExitStack() as exits:
        http, _ = await service(exits)
        action = (await chat(http, OFFLINE, STILL_DOWN)).json()["pending_action"]["id"]
        first, second = await asyncio.gather(
            chat(http, OFFLINE, "Confirmo", action), chat(http, OFFLINE, "Confirmo", action)
        )
        replay = await chat(http, OFFLINE, "Confirmo de novo", action)

    receipts = {response.json()["answer"] for response in (first, second, replay)}
    assert [row[1:] for row in tickets(tmp_path)] == [(OFFLINE, "GC-1180-7742")]
    assert receipts == {
        load_prompt("ticket_opened").text.format(
            ticket=tickets(tmp_path)[0][0], terminal="GC-1180-7742"
        )
    }


async def test_another_merchants_action_id_is_404(service, tmp_path):
    async with AsyncExitStack() as exits:
        http, _ = await service(exits)
        action = (await chat(http, OFFLINE, STILL_DOWN)).json()["pending_action"]["id"]
        await chat(http, OFFLINE, "Confirmo", action)
        stolen = await chat(http, ONLINE, "Confirmo", action)
        unknown = await chat(http, OFFLINE, "Confirmo", "0" * 32)

    assert stolen.status_code == unknown.status_code == 404
    assert len(tickets(tmp_path)) == 1


async def test_a_vendor_failure_mid_write_leaves_no_ticket(service, tmp_path, monkeypatch):
    async with AsyncExitStack() as exits:
        http, storage = await service(exits)
        action = (await chat(http, OFFLINE, STILL_DOWN)).json()["pending_action"]["id"]

        async def unreachable(*args, **kwargs):
            raise ConnectionError("ticket backend unreachable")

        monkeypatch.setattr(storage.desk, "open", unreachable)
        failed = (await chat(http, OFFLINE, "Confirmo", action)).json()
        replay = await chat(http, OFFLINE, "Confirmo", action)

    assert failed["status"] == "pending_human"
    assert failed["escalation"]["reason"] == "tool_unavailable"
    assert tickets(tmp_path) == [] and replay.status_code == 404


async def test_an_online_terminal_gets_no_proposal(service, tmp_path):
    async with AsyncExitStack() as exits:
        http, _ = await service(exits)
        reply = (await chat(http, ONLINE, STILL_DOWN)).json()

    assert reply["pending_action"] is None
    assert reply["escalation"]["reason"] == "individual_case"
    assert tickets(tmp_path) == []


async def test_the_merchant_is_told_once_it_is_automated_and_never_to_repeat_themselves(service):
    async with AsyncExitStack() as exits:
        http, _ = await service(exits)
        first = (await chat(http, ONLINE, STILL_DOWN)).json()
        second = (await chat(http, ONLINE, "Alguma novidade?")).json()

    disclosure = load_prompt("ai_disclosure").text
    assert first["answer"].startswith(disclosure)
    assert "não precisa repetir" in first["answer"], "a handoff says the operator has the summary"
    assert disclosure not in second["answer"]
