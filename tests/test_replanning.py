"""Bounded replanning: a step's finding adds one read step for the other specialist, an added step
that does not answer is dropped, and a spent budget stops the turn with its answered part."""

import json
from datetime import date

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.contracts import EscalationReason, Intent, Need, TargetAgent
from app.graph.build import after_specialist, build_graph
from app.graph.build import knowledge as knowledge_node
from app.graph.compose import ComposedAnswer
from app.graph.human_review import HandoffDraft
from app.graph.knowledge import KnowledgeAnswer
from app.graph.memory import thread_id, turn_input
from app.graph.router import PlanStep, RouterDecision, route
from app.graph.state import GraphState
from app.graph.supervisor import revise
from app.graph.support import SupportAction, SupportAnswer, SupportPlan
from app.observability import configure_logging
from app.observability.metrics import charge, metered
from app.rag.retrieve import HybridRetriever
from app.settings import Settings

FEES = "A mensalidade é R$ 14,90 na Get Mini, R$ 79,90 na Get Clássica e R$ 129,90 na Get Smart."
MODEL_NEED = Need(agent=TargetAgent.SUPPORT, query_pt="Qual é o modelo da maquininha do cliente?")
COMPOSED = "Sua maquininha é a Get Smart, cuja mensalidade é de R$ 129,90."
PROFILE = SupportPlan(action=SupportAction.CALL_TOOL, tool="get_account_profile")
SUPPORT, KNOWLEDGE = TargetAgent.SUPPORT, TargetAgent.KNOWLEDGE


def plan(intent: Intent, *steps: tuple[TargetAgent, str]) -> RouterDecision:
    planned = [PlanStep(agent=agent, query_pt=query) for agent, query in steps]
    return RouterDecision(intent=intent, confidence=0.9, rationale="t", steps=planned)


class Model:
    """Answers each schema by name, charging `cost_usd` per call as a vendor would."""

    def __init__(self, cost_usd: float = 0.0, **overrides) -> None:
        self.answers = {
            "RouterDecision": plan(Intent.PRODUCT_INFO, (KNOWLEDGE, "mensalidade")),
            "KnowledgeAnswer": KnowledgeAnswer(coverage="partial", answer=FEES, needs=MODEL_NEED),
            "SupportPlan": PROFILE,
            "SupportAnswer": SupportAnswer(answer="Sua maquininha é a Get Smart."),
            "ComposedAnswer": ComposedAnswer(answer=COMPOSED),
            "HandoffDraft": HandoffDraft(draft="?"),
        } | overrides
        self.cost_usd, self.calls = cost_usd, []

    async def structured(self, *, system: str, user: str, schema: type):
        self.calls.append((schema.__name__, user))
        charge(cost_usd=self.cost_usd)
        return self.answers[schema.__name__]

    def inputs(self, schema: str) -> list[str]:
        return [user for name, user in self.calls if name == schema]


@pytest.fixture
def send(sample_chunks, fake_collection):
    async def run(model: Model, caller: str = "cliente1988", **settings) -> dict:
        scores = fake_collection({chunk.id: 0.9 for chunk in sample_chunks})
        retriever = HybridRetriever(sample_chunks, scores, top_k=5)
        graph = build_graph(model, Settings(**settings), retriever, InMemorySaver())
        turn = turn_input(
            message="m", user_id=caller, conversation_id="c", trace_id="t", today=date(2026, 9, 1)
        )
        with metered():
            return await graph.ainvoke(
                turn, {"configurable": {"thread_id": thread_id(caller, "c")}}
            )

    return run


@pytest.mark.parametrize(("changes", "answer", "steps"), [(3, COMPOSED, 2), (0, FEES, 1)])
async def test_a_finding_adds_a_read_step_unless_plan_changes_are_off(send, changes, answer, steps):
    model = Model()

    final = await send(model, max_plan_changes=changes)

    assert final["answer"] == answer and len(final["step_results"]) == steps
    if changes:
        assert json.loads(model.inputs("SupportPlan")[0])["question"] == MODEL_NEED.query_pt
        assert '"model": "Get Smart"' in model.inputs("SupportAnswer")[0], "the profile was read"
        parts = json.loads(model.inputs("ComposedAnswer")[0])["parts"]
        assert [part.get("lookup", False) for part in parts] == [False, True]


@pytest.mark.parametrize(
    "action", ["escalate_individual_case", "handoff_to_knowledge", "propose_technical_ticket"]
)
async def test_an_added_step_that_does_not_answer_from_a_read_is_dropped(send, action):
    """No escalation, no second router pass, no proposal: the answer stands as it was."""
    model = Model(SupportPlan=SupportPlan(action=action, symptom="não liga"))

    final = await send(model, caller="cliente2042")  # its terminal is offline: a write is possible

    assert final["answer"] == FEES and len(model.inputs("RouterDecision")) == 1
    assert final["escalation"] is None and final["pending_action"] is None


@pytest.mark.parametrize(
    ("budget", "cost_usd", "limit"), [("max_model_calls", 0, 3), ("max_cost_usd", 0.01, 0.02)]
)
async def test_a_spent_budget_sends_the_answered_part_and_hands_the_rest_over(
    send, capsys, budget, cost_usd, limit
):
    configure_logging()
    compound = plan(Intent.ACCOUNT_DATA, (SUPPORT, "a"), (KNOWLEDGE, "b"))
    model = Model(cost_usd, RouterDecision=compound, SupportAnswer=SupportAnswer(answer="Parte."))

    final = await send(model, **{budget: limit})

    assert not model.inputs("KnowledgeAnswer"), "the second step never started"
    assert final["escalation"].reason is EscalationReason.BUDGET_EXHAUSTED
    assert final["partial_answer"] == "Parte."
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line[:1] == "{"]
    assert next(e for e in events if e["event"] == "budget.exhausted")["budget"] == budget


def answered(need: Need | None, **fields) -> GraphState:
    """A knowledge step that answered, first of a two-step plan whose second step asks "bbb"."""
    state = GraphState(message="m", user_id="c", answer="ok", need=need, **fields)
    state.route = plan(Intent.PRODUCT_INFO, (KNOWLEDGE, "aaa"), (SUPPORT, "bbb"))
    return state


@pytest.mark.parametrize(
    ("state", "calls"),
    [
        (answered(Need(agent=KNOWLEDGE, query_pt="outra")), 12),
        (answered(MODEL_NEED, refusal_reason="individual_case"), 12),
        (answered(MODEL_NEED, plan_changes=3), 12),
        (answered(Need(agent=SUPPORT, query_pt="bbb")), 12),
        (answered(MODEL_NEED), 1),
    ],
    ids=["same agent", "no answer", "changes spent", "repeated step", "budget spent"],
)
def test_a_need_adds_nothing_past_the_turns_limits(state, calls):
    with metered():
        charge(calls=1)
        assert revise(state, Settings(max_model_calls=calls)) == {}


def test_a_hand_back_with_the_model_budget_spent_does_not_replan():
    state = answered(None, handoff_reason=EscalationReason.REPEATED_MISROUTE.value)

    assert after_specialist(state, Settings(max_model_calls=1)) == "router", "outside a turn"
    with metered():
        charge(calls=1)
        assert after_specialist(state, Settings(max_model_calls=1)) == "collect"


async def test_an_added_knowledge_step_reads_the_corpus_even_on_a_web_turn(
    sample_chunks, fake_collection
):
    """Its question may carry what the account showed, which never goes into a web query."""
    state = GraphState(message="m", user_id="c", step_index=1)
    state.route = plan(Intent.GENERAL_INFO, (SUPPORT, "a"))
    state.plan = [*state.route.steps, PlanStep(agent=KNOWLEDGE, query_pt="b", requested_by=SUPPORT)]
    model = Model(KnowledgeAnswer=KnowledgeAnswer(coverage="full", answer="Do site."))
    retriever = HybridRetriever(sample_chunks, fake_collection(), top_k=3)

    update = await knowledge_node(state, model, retriever, threshold=0.0, web=None)

    assert update["answer"] == "Do site."


async def test_only_the_supervisor_can_mark_a_step_as_added():
    """The field is absent from the schema the vendor sees, and cleared if a plan carries it."""
    marked = PlanStep(agent=SUPPORT, query_pt="q", requested_by=KNOWLEDGE)
    decision = RouterDecision(
        intent=Intent.ACCOUNT_DATA, confidence=0.9, rationale="t", steps=[marked]
    )

    update = await route(GraphState(message="q", user_id="c"), Model(RouterDecision=decision), 6)

    assert "requested_by" not in json.dumps(RouterDecision.model_json_schema())
    assert update["plan"][0].requested_by is None
