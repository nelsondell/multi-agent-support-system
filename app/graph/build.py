"""Graph assembly.

Routing policy is readable here rather than hidden inside nodes: the confidence threshold is the
conditional edge out of the router, the plan is walked one step at a time by `collect`, and
`compose` joins the steps. Persistence is attached here too, and only here: the checkpointer
arrives from the caller and no node sees it.
"""

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.graph.compose import compose
from app.graph.escalation import escalate
from app.graph.guardrail import after_guardrail
from app.graph.guardrail import inspect as guardrail_inspect
from app.graph.human_review import await_operator, prepare_handoff
from app.graph.knowledge import answer as knowledge_answer
from app.graph.memory import remember
from app.graph.router import route
from app.graph.state import GraphState, StepResult
from app.graph.support import answer as support_answer
from app.observability import instrument_node
from app.providers.llm import LLMProvider
from app.rag.retrieve import HybridRetriever
from app.settings import Settings

if TYPE_CHECKING:
    from app.handoffs import HandoffQueue


def dispatch(state: GraphState, threshold: float) -> Literal["knowledge", "support", "escalate"]:
    """Start the plan only when the router is confident enough."""
    if state.route is None or state.route.confidence < threshold:
        return "escalate"
    return state.route.steps[state.step_index].agent.value


async def knowledge(
    state: GraphState,
    provider: LLMProvider,
    retriever: HybridRetriever,
    threshold: float,
) -> dict[str, Any]:
    """Retrieve on the running step's Portuguese query, then answer from what came back."""
    query = state.current_query()
    return await knowledge_answer(state, provider, retriever.search(query), threshold)


def after_specialist(state: GraphState, max_hops: int) -> Literal["router", "collect"]:
    """Where a specialist's outcome goes next.

    A hand-off means the specialist believes its step belongs to the other one, so the plan was
    wrong: the router replans the whole turn, but only while the hop budget lasts, so two agents
    cannot pass the same message back and forth. Anything else is that step's result: an answer,
    a refusal, or a hand-off with the budget spent.
    """
    if state.handoff_reason and state.hops < max_hops:
        return "router"
    return "collect"


async def collect(state: GraphState) -> dict[str, Any]:
    """Record the step that just ran, then clear the per-step fields for the next one."""
    step = state.current_step()
    result = StepResult(
        agent=step.agent,
        query_pt=state.current_query(),
        answer=state.answer,
        sources=state.sources,
        refusal_reason=state.refusal_reason or state.handoff_reason,
    )
    return {
        "step_results": [*state.step_results, result],
        "step_index": state.step_index + 1,
        "answer": None,
        "sources": [],
        "refusal_reason": None,
        "handoff_reason": None,
    }


def after_collect(state: GraphState) -> Literal["knowledge", "support", "compose"]:
    """The next step's specialist, or the join once the plan is done."""
    step = state.current_step()
    return step.agent.value if step else "compose"


def after_compose(state: GraphState) -> Literal["escalate", "remember"]:
    """A step that needs a human sends the turn to `human_review`, answered part and all."""
    return "escalate" if state.refusal_reason else "remember"


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
        "collect": collect,
        "compose": partial(compose, provider=provider),
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


def _wire(graph: StateGraph, settings: Settings) -> None:
    """Every edge of the graph, in the order a turn travels them."""
    specialists = {"knowledge": "knowledge", "support": "support"}
    graph.add_edge(START, "guardrail")
    graph.add_conditional_edges("guardrail", after_guardrail, {"router": "router", "done": END})
    graph.add_conditional_edges(
        "router",
        partial(dispatch, threshold=settings.router_confidence_threshold),
        {**specialists, "escalate": "escalate"},
    )
    outcome = partial(after_specialist, max_hops=settings.max_hops)
    for specialist in specialists:
        graph.add_conditional_edges(specialist, outcome, {"router": "router", "collect": "collect"})
    graph.add_conditional_edges("collect", after_collect, {**specialists, "compose": "compose"})
    graph.add_conditional_edges(
        "compose", after_compose, {"escalate": "escalate", "remember": "remember"}
    )
    # A guardrail block goes straight to END above, so a refused message is never remembered.
    # An escalation is remembered too, but only once the operator has answered it.
    graph.add_edge("escalate", "prepare_handoff")
    graph.add_edge("prepare_handoff", "await_operator")
    graph.add_edge("await_operator", "remember")
    graph.add_edge("remember", END)


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
    _wire(graph, settings)
    return graph.compile(checkpointer=checkpointer)
