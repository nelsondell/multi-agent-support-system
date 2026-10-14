"""The terminal handoff.

Deterministic and model-free: the reason is a typed enum rather than a sentence, so it can be
counted in a dashboard and asserted in a test.
"""

from typing import Any
from uuid import uuid4

from app.contracts import Escalation, EscalationReason
from app.graph.state import GraphState
from app.prompts import load_prompt

_HANDOFF_PROMPT = "escalation"
_UNSUPPORTED_PROMPT = "not_implemented"


def mint_protocol() -> str:
    """A protocol number the merchant can quote to a human agent."""
    return f"ESC-{uuid4().hex[:10].upper()}"


def resolve_reason(state: GraphState) -> EscalationReason:
    """A refusal is terminal, a hand-off is a retry that ran out of budget."""
    raw = state.refusal_reason or state.handoff_reason
    if raw:
        return EscalationReason(raw)
    return EscalationReason.LOW_ROUTER_CONFIDENCE


async def escalate(state: GraphState) -> dict[str, Any]:
    """Close the request with a handoff payload and a Portuguese message."""
    reason = resolve_reason(state)
    protocol = mint_protocol()
    body = state.answer or (
        load_prompt(_UNSUPPORTED_PROMPT).text
        if reason is EscalationReason.AGENT_NOT_IMPLEMENTED
        else ""
    )
    handoff = load_prompt(_HANDOFF_PROMPT).text.format(protocol=protocol)
    return {
        "answer": f"{body}\n\n{handoff}".strip(),
        "sources": state.sources,
        "escalation": Escalation(
            protocol=protocol,
            reason=reason,
            summary=state.masked_message or state.message,
        ),
    }
