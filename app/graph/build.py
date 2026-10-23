"""Graph assembly.

Routing policy is readable here rather than hidden inside nodes: the confidence threshold is the
conditional edge out of the router, the plan is walked one step at a time by `collect`, which
lets the supervisor revise it within the turn's budgets (`supervisor.py`), and `compose` joins
the steps. A step that needs a person pauses the turn right there; once the
operator answers, the plan picks up where it stopped. Persistence is attached here too, and only
here: the checkpointer arrives from the caller and no node sees it.
"""

from collections.abc import Callable
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import NodeError, NodeTimeoutError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy, TimeoutPolicy

from app.contracts import EscalationReason, Intent
from app.graph.actions import confirm_action
from app.graph.compose import compose
from app.graph.escalation import escalate
from app.graph.guardrail import after_guardrail
from app.graph.guardrail import inspect as guardrail_inspect
from app.graph.human_review import await_operator, prepare_handoff
from app.graph.knowledge import answer as knowledge_answer
from app.graph.knowledge import answer_from_web, retrieve
from app.graph.memory import remember
from app.graph.router import route
from app.graph.state import GraphState, StepResult
from app.graph.supervisor import Metered, dropped, exhausted, halt, report, revise
from app.graph.support import answer as support_answer
from app.observability import get_logger, instrument_node
from app.observability.metrics import VENDOR_FAILURES
from app.providers.llm import LLMProvider, LLMProviderError, LLMTimeout
from app.rag.retrieve import HybridRetriever
from app.settings import Settings

if TYPE_CHECKING:
    from app.handoffs import HandoffQueue
    from app.tools.tickets import TicketDesk
    from app.tools.web import WebSearch

logger = get_logger(__name__)

MODEL_FAILURES = (LLMProviderError, NodeTimeoutError)
"""What a node is retried on, and what escalates once its attempts run out."""

ESCALATE_ON_FAILURE = ("router", "knowledge", "support")
"""The nodes whose model call has no answer to fall back on. The others degrade inside the node:
compose joins the step answers, a draft is left out, a rephrasing gives way to the operator's
text, a summary fold keeps the history whole."""


async def model_failed(state: GraphState, error: NodeError) -> Command:
    """Once every attempt failed or timed out, the turn escalates as `model_unavailable`: from the
    router straight away, from a plan step through `collect`, so an answered step still goes out.
    Any other exception is a bug, and fails the run."""
    if not isinstance(error.error, MODEL_FAILURES):
        raise error.error
    logger.warning("node.failed", node=error.node, error=type(error.error).__name__)
    kind = "timeout" if isinstance(error.error, (NodeTimeoutError, LLMTimeout)) else "error"
    VENDOR_FAILURES.labels(vendor="model", kind=kind).inc()
    update = {"refusal_reason": EscalationReason.MODEL_UNAVAILABLE.value}
    if error.node == "router":
        return Command(update=update, goto="escalate")
    return Command(update={**update, "answer": None, "sources": []}, goto="collect")


def dispatch(state: GraphState, threshold: float) -> Literal["knowledge", "support", "escalate"]:
    """Start the plan only when the router is confident enough."""
    step = state.current_step()
    if state.route is None or step is None or state.route.confidence < threshold:
        return "escalate"
    return step.agent.value


async def knowledge(
    state: GraphState,
    provider: LLMProvider,
    retriever: HybridRetriever,
    threshold: float,
    web: "WebSearch | None",
) -> dict[str, Any]:
    """A general-purpose question goes to the web; anything else is retrieved from the corpus.

    Never both: a product question the corpus cannot answer abstains, it does not search the web.
    A step the supervisor added always reads the corpus: its question may carry what the
    merchant's account showed, which never goes into a web query.
    """
    step = state.current_step()
    added = step is not None and step.requested_by is not None
    if state.route is not None and state.route.intent is Intent.GENERAL_INFO and not added:
        return await answer_from_web(state, provider, web)
    return await knowledge_answer(state, provider, retrieve(state, retriever), threshold)


def after_specialist(
    state: GraphState, settings: Settings
) -> Literal["router", "collect", "confirm_action"]:
    """Where a specialist's outcome goes next.

    A hand-off means the specialist believes its step belongs to the other one, so the plan was
    wrong: the router replans the whole turn, but only while the hop and model budgets last, so
    two agents cannot pass the same message back and forth. Anything else is that step's result:
    an answer, a refusal, or a hand-off with a budget spent. A step the supervisor added goes to
    `collect` whatever it did: it neither replans the turn nor proposes a write.
    """
    step = state.current_step()
    if step is not None and step.requested_by is not None:
        return "collect"
    if state.handoff_reason:
        budget = "max_hops" if state.hops >= settings.max_hops else exhausted(settings)
        if budget is None:
            return "router"
        report(budget)
    return "confirm_action" if state.pending_action else "collect"


async def collect(state: GraphState, settings: Settings) -> dict[str, Any]:
    """Record the step that just ran, let the supervisor revise the rest of the plan, and clear
    the per-step fields for the next step."""
    cleared = {
        "step_index": state.step_index + 1,
        "answer": None,
        "sources": [],
        "refusal_reason": None,
        "handoff_reason": None,
        "need": None,
    }
    if dropped(state):
        logger.info("plan.step_dropped", agent=state.current_step().agent.value)
        return {**cleared, "pending_action": None}
    step = state.current_step()
    result = StepResult(
        agent=step.agent,
        query_pt=state.current_query(),
        requested_by=step.requested_by,
        answer=state.answer,
        sources=state.sources,
        refusal_reason=state.refusal_reason or state.handoff_reason,
    )
    update = {**cleared, "step_results": [*state.step_results, result], **revise(state, settings)}
    if result.refusal_reason:  # the turn pauses here for a person, budget or not
        return update
    plan = update.get("plan", state.steps)
    return {**update, **halt(plan, update["step_index"], update["step_results"], settings)}


def after_collect(state: GraphState) -> Literal["knowledge", "support", "compose"]:
    """The next step's specialist, or the join once the plan is done. A step that needs a person
    goes to the join at once, so the turn pauses there and the rest of the plan runs after the
    operator answers, with that answer in hand."""
    last = state.step_results[-1] if state.step_results else None
    if last is not None and last.refusal_reason:
        return "compose"
    step = state.current_step()
    return step.agent.value if step else "compose"


def after_operator(state: GraphState) -> Literal["knowledge", "support", "compose", "remember"]:
    """Back into the plan once the operator answered: its remaining steps, then the join. A turn
    the operator closed outright (a cancelled case, or one that never reached a specialist) has
    its answer already and is remembered."""
    if state.answer:
        return "remember"
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
    web: "WebSearch | None",
    desk: "TicketDesk | None",
) -> dict[str, Callable]:
    """Every node of the graph, with its dependencies bound."""
    return {
        "confirm_action": partial(confirm_action, desk=desk),
        "guardrail": guardrail_inspect,
        "router": partial(route, provider=provider, window=settings.history_window),
        "knowledge": partial(
            knowledge,
            provider=provider,
            retriever=retriever,
            threshold=settings.retrieval_confidence_threshold,
            web=web,
        ),
        "support": partial(support, provider=provider),
        "collect": partial(collect, settings=settings),
        "compose": partial(compose, provider=provider),
        "escalate": escalate,
        "prepare_handoff": partial(
            prepare_handoff,
            provider=provider,
            queue=queue,
            ttl=timedelta(hours=settings.handoff_ttl_hours),
        ),
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
    outcome = partial(after_specialist, settings=settings)
    for specialist in specialists:
        graph.add_conditional_edges(
            specialist,
            outcome,
            {"router": "router", "collect": "collect", "confirm_action": "confirm_action"},
        )
    # A proposed write waits there for the merchant, then joins the plan like any step result.
    graph.add_edge("confirm_action", "collect")
    graph.add_conditional_edges("collect", after_collect, {**specialists, "compose": "compose"})
    graph.add_conditional_edges(
        "compose", after_compose, {"escalate": "escalate", "remember": "remember"}
    )
    # A guardrail block goes straight to END above, so a refused message is never remembered.
    # An escalation is remembered too, but only once the operator has answered it.
    graph.add_edge("escalate", "prepare_handoff")
    graph.add_edge("prepare_handoff", "await_operator")
    graph.add_conditional_edges(
        "await_operator",
        after_operator,
        {**specialists, "compose": "compose", "remember": "remember"},
    )
    graph.add_edge("remember", END)


def build_graph(
    provider: LLMProvider,
    settings: Settings,
    retriever: HybridRetriever,
    checkpointer: BaseCheckpointSaver | None = None,
    queue: "HandoffQueue | None" = None,
    web: "WebSearch | None" = None,
    desk: "TicketDesk | None" = None,
):
    """Compile the orchestration graph, persisting each thread when a checkpointer is given.

    An escalated turn pauses for a human, which needs the checkpointer; the queue is where the
    operator finds the case. Without `web`, general-purpose questions get the degraded answer.
    Every node gets LangGraph's own timeout and retry, and the nodes in `ESCALATE_ON_FAILURE` its
    error handler, in place of a try/except in each. Every model call counts against the turn's
    budget.
    """
    graph = StateGraph(GraphState)
    graph.set_node_defaults(
        retry_policy=RetryPolicy(max_attempts=settings.node_attempts, retry_on=MODEL_FAILURES),
        timeout=TimeoutPolicy(run_timeout=settings.node_timeout_s),
    )
    metered = Metered(provider)
    for name, node in graph_nodes(metered, settings, retriever, queue, web, desk).items():
        handler = model_failed if name in ESCALATE_ON_FAILURE else None
        graph.add_node(name, instrument_node(name, node), error_handler=handler)
    _wire(graph, settings)
    return graph.compile(checkpointer=checkpointer)
