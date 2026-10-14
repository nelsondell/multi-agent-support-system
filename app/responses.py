"""Projection of a thread's graph state onto the public `ChatResponse` contract."""

from typing import Any

from app.contracts import ChatResponse, Intent
from app.graph.state import GraphState
from app.prompts import load_prompt

PAUSED_AT = "await_operator"
"""The node a thread waits in while a human has its case."""


def _project(
    state: GraphState, conversation_id: str, pending: bool, answer: str, cite: bool = True
) -> ChatResponse:
    decision = state.route
    return ChatResponse(
        conversation_id=conversation_id,
        status="pending_human" if pending else "answered",
        answer=answer,
        intent=decision.intent if decision else Intent.OTHER,
        agents=[result.agent for result in state.step_results],
        confidence=decision.confidence if decision else 0.0,
        sources=state.sources if cite else [],
        escalated=state.escalation is not None,
        escalation=state.escalation,
    )


def to_response(final: dict[str, Any], conversation_id: str) -> ChatResponse:
    """The result of a run: answered, or paused for a human when it ended in `interrupt()`. A
    paused turn still cites the sources of any part it already answered."""
    state = GraphState.model_validate(final)
    pending = bool(final.get("__interrupt__"))
    return _project(state, conversation_id, pending, state.answer or "")


def pending_response(values: dict[str, Any], conversation_id: str) -> ChatResponse:
    """What a message gets while its conversation waits for a human: the open protocol."""
    state = GraphState.model_validate(values)
    notice = load_prompt("pending_human").text.format(protocol=state.escalation.protocol)
    return _project(state, conversation_id, True, notice, cite=False)
