"""The one write, a technical ticket: proposed by the agent, run only on the merchant's
confirmation, once. cliente2042's terminal is offline in the mock data; cliente1988's is not."""

import asyncio
import json
import sqlite3
from contextlib import AsyncExitStack

from app.contracts import Intent, TargetAgent
from app.graph.human_review import HandoffDraft
from app.graph.router import PlanStep, RouterDecision
from app.graph.support import SupportAction, SupportAnswer, SupportPlan
from app.prompts import load_prompt
from app.tools.tickets import TicketDesk
from tests.identity import IDENTITY

OFFLINE, ONLINE = "cliente2042", "cliente1988"
STILL_DOWN = "Minha maquininha continua sem conexão mesmo depois de reiniciar e testar o chip."
SYMPTOM = "sem conexão depois de reiniciar e testar o chip"
OPERATION = f'open_technical_ticket(terminal_id="GC-1180-7742", symptom="{SYMPTOM}")'


class Model:
    async def structured(self, *, system: str, user: str, schema: type):
        if schema is RouterDecision:
            step = PlanStep(agent=TargetAgent.SUPPORT, query_pt=json.loads(user)["message"])
            return RouterDecision(
                intent=Intent.TROUBLESHOOTING, confidence=0.9, rationale="t", steps=[step]
            )
        if schema is SupportPlan and "sem conexão" in user:
            return SupportPlan(action=SupportAction.PROPOSE_TECHNICAL_TICKET, symptom=SYMPTOM)
        if schema is SupportPlan:
            return SupportPlan(action=SupportAction.CALL_TOOL, tool="get_transactions")
        if schema is SupportAnswer:
            return SupportAnswer(answer="Suas vendas recentes estão em dia.")
        return HandoffDraft(draft="Estamos verificando.")


async def chat(http, merchant: str, message: str, confirm: str | None = None):
    payload = {"message": message, "conversation_id": "c1", "confirm_action_id": confirm}
    return await http.post("/chat", json=payload, headers=IDENTITY.merchant(merchant))


def tickets(tmp_path) -> list:
    with sqlite3.connect(tmp_path / "state" / "handoffs.sqlite") as connection:
        return connection.execute("SELECT id, user_id, terminal_id FROM tickets").fetchall()


async def test_a_proposal_waits_for_confirmation_and_only_an_offline_terminal_gets_one(
    api, tmp_path
):
    """The first reply also says it is automated, and a handoff that the summary went along."""
    async with AsyncExitStack() as exits:
        http = await api(exits, Model())
        proposed = (await chat(http, OFFLINE, STILL_DOWN)).json()
        moved_on = (await chat(http, OFFLINE, "Deixa pra lá. Como estão minhas vendas?")).json()
        online = (await chat(http, ONLINE, STILL_DOWN)).json()
        audit = await http.get("/audit", headers=IDENTITY.operator("bia", "supervisor"))

    action, disclosure = proposed["pending_action"], load_prompt("ai_disclosure").text
    assert proposed["status"] == "pending_confirmation" and action["operation"] == OPERATION
    assert action["operation"] in proposed["answer"] and proposed["answer"].startswith(disclosure)
    assert moved_on["answer"] == "Suas vendas recentes estão em dia.", "cancelled, not repeated"
    assert [entry["action"] for entry in audit.json()][:2] == [
        "action.cancelled",
        "action.proposed",
    ]
    assert online["pending_action"] is None and online["escalation"]["reason"] == "individual_case"
    assert "não precisa repetir" in online["answer"] and tickets(tmp_path) == []


async def test_confirmations_write_once_and_only_for_their_merchant(api, tmp_path):
    async with AsyncExitStack() as exits:
        http = await api(exits, Model())
        action = (await chat(http, OFFLINE, STILL_DOWN)).json()["pending_action"]["id"]
        first, second = await asyncio.gather(
            chat(http, OFFLINE, "Confirmo", action), chat(http, OFFLINE, "Confirmo", action)
        )
        replay = await chat(http, OFFLINE, "Confirmo de novo", action)
        stolen = await chat(http, ONLINE, "Confirmo", action)
        unknown = await chat(http, OFFLINE, "Confirmo", "0" * 32)

    (ticket,) = tickets(tmp_path)
    assert ticket[1:] == (OFFLINE, "GC-1180-7742")
    receipt = load_prompt("ticket_opened").text.format(ticket=ticket[0], terminal="GC-1180-7742")
    assert {response.json()["answer"] for response in (first, second, replay)} == {receipt}
    assert stolen.status_code == unknown.status_code == 404


async def test_a_backend_failure_mid_write_leaves_no_ticket(api, tmp_path, monkeypatch):
    async def unreachable(*args, **kwargs):
        raise ConnectionError("ticket backend unreachable")

    monkeypatch.setattr(TicketDesk, "open", unreachable)
    async with AsyncExitStack() as exits:
        http = await api(exits, Model())
        action = (await chat(http, OFFLINE, STILL_DOWN)).json()["pending_action"]["id"]
        failed = (await chat(http, OFFLINE, "Confirmo", action)).json()
        replay = await chat(http, OFFLINE, "Confirmo", action)

    assert failed["escalation"]["reason"] == "tool_unavailable"
    assert tickets(tmp_path) == [] and replay.status_code == 404
