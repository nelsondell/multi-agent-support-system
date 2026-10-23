"""LangGraph's own retry, timeout and error handler, in place of a try/except in each node: a
model failure is retried, then escalated as model_unavailable; a hung call is cut by the node
timeout; a bug still fails the run instead of passing for an escalation."""

import asyncio
from collections import Counter

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.contracts import EscalationReason, Intent, TargetAgent
from app.graph.human_review import HandoffDraft
from app.graph.router import PlanStep, RouterDecision
from app.graph.state import GraphState
from app.graph.support import SupportAction, SupportAnswer, SupportPlan
from app.prompts import load_prompt
from app.providers.llm import LLMProviderError

TODAY_ANSWER = "O dinheiro das vendas de ontem, R$ 231,26 líquidos, cai hoje."
ANSWERED = SupportAnswer(answer=TODAY_ANSWER)


def plan(*queries: str) -> RouterDecision:
    steps = [PlanStep(agent=TargetAgent.SUPPORT, query_pt=query) for query in queries]
    return RouterDecision(intent=Intent.ACCOUNT_DATA, confidence=0.9, rationale="t", steps=steps)


class Model:
    """Scripted answers; a schema in `failures` fails that many times first, and an answer may be
    an async function of the input, to hang or to raise."""

    def __init__(self, answers: dict, failures: dict | None = None) -> None:
        self.answers, self.failures, self.calls = answers, failures or {}, Counter()

    async def structured(self, *, system: str, user: str, schema: type):
        self.calls[schema.__name__] += 1
        if self.calls[schema.__name__] <= self.failures.get(schema, 0):
            raise LLMProviderError("vendor 503")
        answer = self.answers[schema]
        return await answer(user) if callable(answer) else answer


def account_model(router, answer=ANSWERED, failures: dict | None = None) -> Model:
    looked_up = SupportPlan(
        action=SupportAction.CALL_TOOL,
        tool="get_settlement_schedule",
        date_from="2026-08-31",
        date_to="2026-08-31",
    )
    draft = HandoffDraft(draft="Estamos verificando.")
    answers = {RouterDecision: router, SupportPlan: looked_up, SupportAnswer: answer}
    return Model({**answers, HandoffDraft: draft}, failures)


async def turn(graph, message: str = "Quando cai o dinheiro de ontem?") -> dict:
    state = GraphState(message=message, user_id="cliente1988")
    return await graph.ainvoke(state, {"configurable": {"thread_id": "c1"}})


async def test_a_transient_model_failure_is_retried_and_the_turn_answers(build_test_graph):
    model = account_model(plan("quando cai o dinheiro de ontem"), failures={RouterDecision: 1})
    graph = build_test_graph(provider=model, checkpointer=InMemorySaver())

    final = await turn(graph)

    assert model.calls["RouterDecision"] == 2
    assert final["answer"] == TODAY_ANSWER
    assert final.get("escalation") is None


async def test_a_model_that_keeps_failing_escalates_as_model_unavailable(build_test_graph):
    model = account_model(plan("quando cai o dinheiro de ontem"), failures={RouterDecision: 9})
    graph = build_test_graph(provider=model, checkpointer=InMemorySaver(), node_attempts=2)

    final = await turn(graph)

    assert model.calls["RouterDecision"] == 2, "one retry, then the fallback"
    assert final["escalation"].reason is EscalationReason.MODEL_UNAVAILABLE
    assert final["answer"].startswith(load_prompt("model_unavailable").text)


async def test_a_hung_step_times_out_and_the_answered_step_still_goes_out(build_test_graph):
    async def hangs_on_tomorrow(user: str) -> SupportAnswer:
        if "amanhã" in user:
            await asyncio.sleep(3600)
        return SupportAnswer(answer=TODAY_ANSWER)

    model = account_model(
        plan("quando cai o dinheiro de ontem", "quando cai o dinheiro de amanhã"),
        answer=hangs_on_tomorrow,
    )
    graph = build_test_graph(provider=model, checkpointer=InMemorySaver(), node_timeout_s=0.2)

    final = await turn(graph, "Quando cai o dinheiro de ontem e o de amanhã?")

    assert model.calls["SupportAnswer"] == 3, "the first step once, the hung one twice"
    assert final["escalation"].reason is EscalationReason.MODEL_UNAVAILABLE
    assert final["partial_answer"] == TODAY_ANSWER


async def test_a_bug_fails_the_run_instead_of_passing_for_an_escalation(build_test_graph):
    async def broken(user: str) -> SupportAnswer:
        raise KeyError("net_amount")

    model = account_model(plan("quando cai o dinheiro de ontem"), answer=broken)
    graph = build_test_graph(provider=model, checkpointer=InMemorySaver())

    with pytest.raises(KeyError, match="net_amount"):
        await turn(graph)
    assert model.calls["SupportAnswer"] == 1, "a bug is not retried"
