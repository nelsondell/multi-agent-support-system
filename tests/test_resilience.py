"""LangGraph's retry, timeout and error handler instead of a try/except per node, and what a
failing vendor costs the merchant: a model is retried then escalates, a hung step is cut, a bug
fails the run, web search down degrades; each vendor failure is counted once."""

import asyncio
from collections import Counter

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.pregel import _retry
from prometheus_client import REGISTRY

from app.contracts import EscalationReason, Intent, TargetAgent
from app.graph.human_review import HandoffDraft
from app.graph.router import PlanStep, RouterDecision
from app.graph.state import GraphState
from app.graph.support import SupportAction, SupportAnswer, SupportPlan
from app.prompts import load_prompt
from app.providers.llm import LLMProviderError, LLMTimeout
from app.settings import Settings
from app.tools.web import WebSearch, WebSearchUnavailable

ANSWER = "O dinheiro das vendas de ontem, R$ 231,26 líquidos, cai hoje."
LOOKUP = SupportPlan(
    action=SupportAction.CALL_TOOL,
    tool="get_settlement_schedule",
    date_from="2026-08-31",
    date_to="2026-08-31",
)


class Model:
    """A plan of one support step per query. Each schema fails `failures[schema]` times first; an
    answer may be an async function of the input, to hang or to raise."""

    def __init__(self, *queries: str, answer=None, failures=None, error=LLMProviderError) -> None:
        steps = [PlanStep(agent=TargetAgent.SUPPORT, query_pt=query) for query in queries or ["q"]]
        router = RouterDecision(
            intent=Intent.ACCOUNT_DATA, confidence=0.9, rationale="t", steps=steps
        )
        self.answers = {
            RouterDecision: router,
            SupportPlan: LOOKUP,
            HandoffDraft: HandoffDraft(draft="?"),
        }
        self.answers[SupportAnswer] = answer or SupportAnswer(answer=ANSWER)
        self.failures, self.error, self.calls = failures or {}, error, Counter()

    async def structured(self, *, system: str, user: str, schema: type):
        self.calls[schema.__name__] += 1
        if self.calls[schema.__name__] <= self.failures.get(schema, 0):
            raise self.error("vendor 500, or no answer in time")
        answer = self.answers[schema]
        return await answer(user) if callable(answer) else answer


async def turn(build_test_graph, model: Model, message: str = "Quando cai?", **settings) -> dict:
    graph = build_test_graph(provider=model, checkpointer=InMemorySaver(), **settings)
    state = GraphState(message=message, user_id="cliente1988")
    return await graph.ainvoke(state, {"configurable": {"thread_id": "c1"}})


def vendor_failures(vendor: str, kind: str) -> float:
    labels = {"vendor": vendor, "kind": kind}
    return REGISTRY.get_sample_value("support_vendor_failures_total", labels) or 0.0


@pytest.mark.parametrize(("error", "kind"), [(LLMProviderError, "error"), (LLMTimeout, "timeout")])
async def test_a_model_that_keeps_failing_escalates_and_counts_once(build_test_graph, error, kind):
    """Every call fails, the draft's too, and the merchant still gets the handoff notice."""
    model = Model(failures={RouterDecision: 9, HandoffDraft: 9}, error=error)
    before = vendor_failures("model", kind)

    final = await turn(build_test_graph, model, node_attempts=2)

    assert model.calls["RouterDecision"] == 2, "one retry, then the fallback"
    assert final["escalation"].reason is EscalationReason.MODEL_UNAVAILABLE
    assert final["answer"].startswith(load_prompt("model_unavailable").text)
    assert vendor_failures("model", kind) - before == 1


async def test_a_hung_step_times_out_and_the_answered_step_still_goes_out(
    build_test_graph, monkeypatch
):
    """No wall clock: the hung step waits on an event that never fires, and LangGraph's run
    timeout fires only once the step has hung, so a slow runner can never cut the step that
    answers."""
    hung, never = asyncio.Event(), asyncio.Event()

    async def fires_once_hung(_run_timeout_s: float) -> None:
        await hung.wait()
        hung.clear()
        raise TimeoutError

    monkeypatch.setattr(_retry, "_run_timeout_watchdog", fires_once_hung)
    asked = Counter()

    async def hangs_on_tomorrow(user: str) -> SupportAnswer:
        asked["amanhã" if "amanhã" in user else "ontem"] += 1
        if "amanhã" in user:
            hung.set()
            await never.wait()
        return SupportAnswer(answer=ANSWER)

    model = Model("o de ontem", "o de amanhã", answer=hangs_on_tomorrow)

    final = await turn(build_test_graph, model, node_timeout_s=60)

    assert asked == {"ontem": 1, "amanhã": 2}, "the first step once, the hung one twice"
    assert final["escalation"].reason is EscalationReason.MODEL_UNAVAILABLE
    assert final["partial_answer"] == ANSWER


async def test_a_bug_fails_the_run_instead_of_passing_for_an_escalation(build_test_graph):
    async def broken(user: str) -> SupportAnswer:
        raise KeyError("net_amount")

    model = Model(answer=broken)

    with pytest.raises(KeyError, match="net_amount"):
        await turn(build_test_graph, model)
    assert model.calls["SupportAnswer"] == 1, "a bug is not retried"


async def test_web_search_down_raises_for_the_degraded_answer_and_counts():
    before = vendor_failures("web", "error")
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(WebSearchUnavailable):
            await WebSearch(Settings(tavily_api_key="tvly-test"), client).search("cotação do euro")

    assert vendor_failures("web", "error") - before == 1
