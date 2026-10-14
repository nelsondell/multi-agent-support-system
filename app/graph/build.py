"""Graph assembly.

Routing policy is readable here rather than hidden inside nodes: the confidence threshold is the
conditional edge out of the router, and the specialists are separate destinations. Persistence is
attached here too, and only here: the checkpointer arrives from the caller and no node sees it.
"""

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.graph.escalation import escalate
from app.graph.guardrail import after_guardrail
from app.graph.guardrail import inspect as guardrail_inspect
from app.graph.human_review import await_operator, prepare_handoff
from app.graph.knowledge import answer as knowledge_answer
from app.graph.memory import remember
from app.graph.router import route
from app.graph.state import GraphState
from app.graph.support import answer as support_answer
from app.observability import instrument_node
from app.providers.llm import LLMProvider
from app.rag.retrieve import HybridRetriever
from app.settings import Settings

if TYPE_CHECKING:
    from app.handoffs import HandoffQueue


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


def graph_nodes(
    provider: LLMProvider,
    settings: Settings,
    retriever: HybridRetriever,
    queue: "HandoffQueue | None",
) -> dict[str, Callable]:
    """Every node of the graph, with its dependencies bound."""
    return {
        "guardrail": guardrail_inspect,
        "router": partial(route, provider=provider, window=settings.history_window),
        "knowledge": partial(
            knowledge,
            provider=provider,
            retriever=retriever,
            threshold=settings.retrieval_confidence_threshold,
        ),
        "support": partial(support, provider=provider),
        "escalate": escalate,
        "prepare_handoff": partial(prepare_handoff, provider=provider, queue=queue),
        "await_operator": partial(await_operator, provider=provider),
        "remember": partial(
            remember,
            provider=provider,
            window=settings.history_window,
            limit=settings.history_max_messages,
        ),
    }


def build_graph(
    provider: LLMProvider,
    settings: Settings,
    retriever: HybridRetriever,
    checkpointer: BaseCheckpointSaver | None = None,
    queue: "HandoffQueue | None" = None,
):
    """Compile the orchestration graph, persisting each thread when a checkpointer is given.

    An escalated turn pauses for a human, which needs the checkpointer; the queue is where the
    operator finds the case.
    """
    graph = StateGraph(GraphState)
    for name, node in graph_nodes(provider, settings, retriever, queue).items():
        graph.add_node(name, instrument_node(name, node))

    graph.add_edge(START, "guardrail")
    graph.add_conditional_edges("guardrail", after_guardrail, {"router": "router", "done": END})
    graph.add_conditional_edges(
        "router",
        partial(pick_specialist, threshold=settings.router_confidence_threshold),
        {"knowledge": "knowledge", "support": "support", "escalate": "escalate"},
    )
    outcome = partial(after_specialist, max_hops=settings.max_hops)
    destinations = {"router": "router", "escalate": "escalate", "done": "remember"}
    graph.add_conditional_edges("knowledge", outcome, destinations)
    graph.add_conditional_edges("support", outcome, destinations)
    # A guardrail block goes straight to END above, so a refused message is never remembered.
    # An escalation is remembered too, but only once the operator has answered it.
    graph.add_edge("escalate", "prepare_handoff")
    graph.add_edge("prepare_handoff", "await_operator")
    graph.add_edge("await_operator", "remember")
    graph.add_edge("remember", END)
    return graph.compile(checkpointer=checkpointer)
