"""Graph assembly.

Routing policy is readable here rather than hidden inside nodes: the confidence threshold is the
conditional edge out of the router, and the specialists are separate destinations.
"""

from functools import partial
from typing import Any, Literal
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from app.contracts import Escalation, EscalationReason
from app.graph.router import route
from app.graph.state import GraphState
from app.prompts import load_prompt
from app.providers.llm import LLMProvider
from app.settings import Settings

_ESCALATION_MESSAGE = "escalation"
_STUB_MESSAGE = "not_implemented"


def _protocol() -> str:
    """Mint a handoff protocol the merchant can quote."""
    return f"ESC-{uuid4().hex[:10].upper()}"


def pick_specialist(
    state: GraphState, threshold: float
) -> Literal["knowledge", "support", "escalate"]:
    """Send the request to a specialist only when the router is confident enough."""
    if state.route is None or state.route.confidence < threshold:
        return "escalate"
    return state.route.target_agent.value


async def knowledge_stub(state: GraphState) -> dict[str, Any]:
    """Placeholder until T-09. It escalates rather than inventing an ungrounded answer."""
    return {"handoff_reason": EscalationReason.AGENT_NOT_IMPLEMENTED.value}


async def support_stub(state: GraphState) -> dict[str, Any]:
    """Placeholder until T-11. It escalates rather than guessing about the merchant's money."""
    return {"handoff_reason": EscalationReason.AGENT_NOT_IMPLEMENTED.value}


async def escalate(state: GraphState) -> dict[str, Any]:
    """Terminal handoff. Deterministic, with no model call."""
    reason = (
        EscalationReason(state.handoff_reason)
        if state.handoff_reason
        else EscalationReason.LOW_ROUTER_CONFIDENCE
    )
    protocol = _protocol()
    body = load_prompt(_STUB_MESSAGE).text if state.handoff_reason else ""
    handoff = load_prompt(_ESCALATION_MESSAGE).text.format(protocol=protocol)
    summary = state.masked_message or state.message
    return {
        "answer": f"{body}\n\n{handoff}".strip(),
        "escalation": Escalation(protocol=protocol, reason=reason, summary=summary),
    }


def build_graph(provider: LLMProvider, settings: Settings):
    """Compile the orchestration graph."""
    graph = StateGraph(GraphState)
    graph.add_node("router", partial(route, provider=provider))
    graph.add_node("knowledge", knowledge_stub)
    graph.add_node("support", support_stub)
    graph.add_node("escalate", escalate)

    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        partial(pick_specialist, threshold=settings.router_confidence_threshold),
        {"knowledge": "knowledge", "support": "support", "escalate": "escalate"},
    )
    graph.add_edge("knowledge", "escalate")
    graph.add_edge("support", "escalate")
    graph.add_edge("escalate", END)
    return graph.compile()
