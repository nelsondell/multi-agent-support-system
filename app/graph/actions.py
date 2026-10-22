"""A write runs only on the merchant's confirmation (Rule of Two), idempotent on the proposal id."""

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from langgraph.types import interrupt
from pydantic import BaseModel

from app.contracts import EscalationReason, Source, SourceType
from app.observability import mask_pii
from app.prompts import load_prompt
from app.tools.account import offline_terminal

if TYPE_CHECKING:
    from app.graph.state import GraphState
    from app.tools.tickets import Ticket, TicketDesk


class ProposedAction(BaseModel):
    id: str
    terminal_id: str
    symptom: str

    @property
    def operation(self) -> str:
        """Rendered by code from the arguments, never summarised by a model."""
        return (
            f"open_technical_ticket(terminal_id={json.dumps(self.terminal_id)}, "
            f"symptom={json.dumps(self.symptom, ensure_ascii=False)})"
        )


class ActionDecision(BaseModel):
    action_id: str | None = None


def propose_ticket(state: "GraphState", symptom: str) -> dict[str, Any]:
    """Only for the merchant's own offline terminal, chosen by code; otherwise a person decides."""
    terminal = offline_terminal(state.user_id)
    if terminal is None:
        return {"refusal_reason": EscalationReason.INDIVIDUAL_CASE.value}
    action = ProposedAction(id=uuid4().hex, terminal_id=terminal, symptom=mask_pii(symptom)[:200])
    text = load_prompt("ticket_proposal").text.format(operation=action.operation, id=action.id)
    return {"pending_action": action, "answer": text, "sources": []}


async def confirm_action(state: "GraphState", desk: "TicketDesk | None") -> dict[str, Any]:
    """The ticket opens only on this proposal's own id; any other message cancels it."""
    action = state.pending_action
    decision = interrupt(action.model_dump(), response_schema=ActionDecision)
    done = {"pending_action": None}
    if decision.action_id != action.id:
        if desk is not None:
            await desk.cancelled(action.id, state.user_id)
        return {**done, "answer": load_prompt("action_cancelled").text, "sources": []}
    try:
        if desk is None:
            raise RuntimeError("no ticket backend is configured")
        ticket = await desk.open(action.id, state.user_id, action.terminal_id, action.symptom)
    except Exception:  # the ticket backend is an external system with its own failures
        return {**done, "answer": None, "refusal_reason": EscalationReason.TOOL_UNAVAILABLE.value}
    text, receipt = receipt_of(ticket)
    return {**done, "answer": text, "sources": [receipt]}


def receipt_of(ticket: "Ticket") -> tuple[str, Source]:
    """The same receipt the first time and on every replay."""
    text = load_prompt("ticket_opened").text.format(ticket=ticket.id, terminal=ticket.terminal_id)
    return text, Source(source_type=SourceType.ACCOUNT, title="open_technical_ticket")
