"""Graph assembly.

Routing policy is readable here rather than hidden inside nodes: the confidence threshold is the
conditional edge out of the router, and the specialists are separate destinations.
"""

from functools import partial
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from app.graph.escalation import escalate
from app.graph.guardrail import after_guardrail
from app.graph.guardrail import inspect as guardrail_inspect
from app.graph.knowledge import answer as knowledge_answer
from app.graph.router import route
from app.graph.state import GraphState
from app.graph.support import answer as support_answer
from app.observability import instrument_node
from app.providers.llm import LLMProvider
from app.rag.retrieve import HybridRetriever
from app.settings import Settings


def pick_specialist(
    state: GraphState, threshold: float
) -> Literal["knowledge", "support", "escalate"]:
    """Send the request to a specialist only when the router is confident enough."""
    if state.route is None or state.route.confidence < threshold:
        return "escalate"
    return state.route.target_agent.value


async def knowledge(
    state: GraphState,
    provider: LLMProvider,
    retriever: HybridRetriever,
    threshold: float,
) -> dict[str, Any]:
    """Retrieve on the router's Portuguese query, then answer from what came back."""
    query = state.route.query_pt if state.route else state.message
    return await knowledge_answer(state, provider, retriever.search(query), threshold)


def after_specialist(state: GraphState, max_hops: int) -> Literal["router", "escalate", "done"]:
    """Where a specialist's outcome goes next.

    A refusal is terminal and escalates. A hand-off means the specialist believes the request
    belongs to the other one: the router gets a second look, but only while the hop budget lasts,
    so two agents cannot pass the same message back and forth.
    """
    if state.refusal_reason:
        return "escalate"
    if state.handoff_reason:
        return "router" if state.hops < max_hops else "escalate"
    return "done"


async def support(state: GraphState, provider: LLMProvider) -> dict[str, Any]:
    """Read the caller's own account and answer from it."""
    return await support_answer(state, provider)


def build_graph(provider: LLMProvider, settings: Settings, retriever: HybridRetriever):
    """Compile the orchestration graph."""
    graph = StateGraph(GraphState)
    nodes = {
        "guardrail": guardrail_inspect,
        "router": partial(route, provider=provider),
        "knowledge": partial(
            knowledge,
            provider=provider,
            retriever=retriever,
            threshold=settings.retrieval_confidence_threshold,
        ),
        "support": partial(support, provider=provider),
        "escalate": escalate,
    }
    for name, node in nodes.items():
        graph.add_node(name, instrument_node(name, node))

    graph.add_edge(START, "guardrail")
    graph.add_conditional_edges("guardrail", after_guardrail, {"router": "router", "done": END})
    graph.add_conditional_edges(
        "router",
        partial(pick_specialist, threshold=settings.router_confidence_threshold),
        {"knowledge": "knowledge", "support": "support", "escalate": "escalate"},
    )
    outcome = partial(after_specialist, max_hops=settings.max_hops)
    destinations = {"router": "router", "escalate": "escalate", "done": END}
    graph.add_conditional_edges("knowledge", outcome, destinations)
    graph.add_conditional_edges("support", outcome, destinations)
    graph.add_edge("escalate", END)
    return graph.compile()
