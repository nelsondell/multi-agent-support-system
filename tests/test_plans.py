"""Plans: up to two specialists per turn, joined by compose, with a partial answer when one of
them needs a human."""

import json
from collections.abc import Callable
from datetime import date

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from app.contracts import Intent, Source, SourceType, TargetAgent
from app.graph.build import build_graph
from app.graph.compose import ComposedAnswer
from app.graph.human_review import HandoffDraft, OperatorReply
from app.graph.knowledge import KnowledgeAnswer
from app.graph.memory import thread_id, turn_input
from app.graph.router import PlanStep, RouterDecision
from app.graph.support import SupportAction, SupportAnswer, SupportPlan
from app.handoffs import open_handoff_queue
from app.rag.retrieve import HybridRetriever
from app.responses import to_response
from app.settings import Settings

CALLER = "cliente1988"
COMPOUND = "Quando cai o dinheiro de ontem e como funciona a antecipação?"
SETTLEMENT_Q = "quando cai o dinheiro das vendas de ontem"
ADVANCE_Q = "como funciona a antecipação de recebíveis"
SETTLEMENT_A = "Consta R$ 396,06 liquidado em 2026-08-31."
ADVANCE_A = "A antecipação adianta os recebíveis das vendas no crédito."
ACCOUNT = Source(source_type=SourceType.ACCOUNT, title="get_settlement_schedule")


class Model:
    """Answers each schema with a preset object or a function of the input; records each call."""

    def __init__(self, answers: dict) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str]] = []

    async def structured(self, *, system: str, user: str, schema: type):
        self.calls.append((schema.__name__, user))
        answer = self.answers[schema]
        return answer(user) if isinstance(answer, Callable) else answer

    def schemas(self) -> list[str]:
        return [schema for schema, _ in self.calls]

    def inputs(self, schema: str) -> list[str]:
        return [user for name, user in self.calls if name == schema]


def plan(*steps: tuple[TargetAgent, str]) -> RouterDecision:
    return RouterDecision(
        intent=Intent.ACCOUNT_DATA,
        confidence=0.9,
        rationale="test",
        steps=[PlanStep(agent=agent, query_pt=query) for agent, query in steps],
    )


COMPOUND_PLAN = plan((TargetAgent.SUPPORT, SETTLEMENT_Q), (TargetAgent.KNOWLEDGE, ADVANCE_Q))


def model(**overrides) -> Model:
    answers = {
        RouterDecision: COMPOUND_PLAN,
        SupportPlan: SupportPlan(
            action=SupportAction.CALL_TOOL,
            tool="get_settlement_schedule",
            date_from="2026-08-31",
            date_to="2026-08-31",
        ),
        SupportAnswer: SupportAnswer(answer=SETTLEMENT_A),
        KnowledgeAnswer: KnowledgeAnswer(sufficient=True, answer=ADVANCE_A, used_urls=[]),
        ComposedAnswer: ComposedAnswer(answer=f"{SETTLEMENT_A} Sobre a antecipação: {ADVANCE_A}"),
        HandoffDraft: HandoffDraft(draft="Nossa equipe está verificando."),
        OperatorReply: lambda user: OperatorReply(answer=json.loads(user)["operator_answer"]),
    }
    by_name = {schema.__name__: schema for schema in answers}
    return Model({**answers, **{by_name[name]: value for name, value in overrides.items()}})


class Harness:
    """Compiles the real graph over the sampled corpus and a handoff queue."""

    def __init__(self, queue, chunks, fake_collection) -> None:
        self.queue = queue
        self._chunks = chunks
        self._collection = fake_collection

    def __call__(self, stand_in: Model, similarity: float = 0.9):
        """`similarity` decides whether the knowledge agent can answer."""
        scores = self._collection({chunk.id: similarity for chunk in self._chunks})
        retriever = HybridRetriever(self._chunks, scores, top_k=5)
        return build_graph(stand_in, Settings(), retriever, InMemorySaver(), self.queue)


@pytest.fixture
async def graph_for(tmp_path, sample_chunks, fake_collection):
    async with open_handoff_queue(Settings(state_dir=str(tmp_path))) as queue:
        yield Harness(queue, sample_chunks, fake_collection)


CONFIG = {"configurable": {"thread_id": thread_id(CALLER, "c1")}}


async def send(graph, message: str = COMPOUND) -> dict:
    turn = turn_input(
        message=message,
        user_id=CALLER,
        conversation_id="c1",
        trace_id="trace",
        today=date(2026, 9, 1),
    )
    return await graph.ainvoke(turn, CONFIG)


async def test_a_compound_request_runs_support_then_knowledge_and_keeps_both_sources(graph_for):
    stand_in = model()

    response = to_response(await send(graph_for(stand_in)), "c1")

    assert stand_in.schemas() == [
        "RouterDecision",
        "SupportPlan",
        "SupportAnswer",
        "KnowledgeAnswer",
        "ComposedAnswer",
    ]
    assert json.loads(stand_in.inputs("SupportPlan")[0])["question"] == SETTLEMENT_Q
    assert ADVANCE_Q in stand_in.inputs("KnowledgeAnswer")[0]
    assert SETTLEMENT_Q not in stand_in.inputs("KnowledgeAnswer")[0]
    assert response.agents == [TargetAgent.SUPPORT, TargetAgent.KNOWLEDGE]
    assert response.sources[0] == ACCOUNT
    assert any(source.source_type is SourceType.KB for source in response.sources)
    assert response.answer == f"{SETTLEMENT_A} Sobre a antecipação: {ADVANCE_A}"
    assert response.status == "answered" and not response.escalated


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        (TargetAgent.KNOWLEDGE, ["RouterDecision", "KnowledgeAnswer"]),
        (TargetAgent.SUPPORT, ["RouterDecision", "SupportPlan", "SupportAnswer"]),
    ],
)
async def test_a_single_intent_request_makes_the_same_calls_as_before(graph_for, agent, expected):
    stand_in = model(RouterDecision=plan((agent, ADVANCE_Q)))

    response = to_response(await send(graph_for(stand_in), ADVANCE_Q), "c1")

    assert stand_in.schemas() == expected
    assert response.agents == [agent]


def test_a_three_step_plan_is_rejected_by_the_schema():
    step = PlanStep(agent=TargetAgent.KNOWLEDGE, query_pt=ADVANCE_Q)

    with pytest.raises(ValidationError):
        RouterDecision(
            intent=Intent.PRODUCT_INFO, confidence=0.9, rationale="test", steps=[step] * 3
        )
    assert RouterDecision.model_json_schema()["properties"]["steps"]["maxItems"] == 2


async def test_a_composed_answer_with_a_new_number_falls_back_to_the_step_answers(graph_for):
    inventive = model(ComposedAnswer=ComposedAnswer(answer="Cai R$ 999,00 amanhã. " + ADVANCE_A))

    final = await send(graph_for(inventive))

    assert final["answer"] == f"{SETTLEMENT_A}\n\n{ADVANCE_A}"


async def test_a_composed_answer_that_only_respells_a_value_is_kept(graph_for):
    respelled = f"O valor 396.06 foi liquidado em 2026-08-31. {ADVANCE_A}"

    final = await send(graph_for(model(ComposedAnswer=ComposedAnswer(answer=respelled))))

    assert final["answer"] == respelled


async def test_one_step_answers_now_and_the_other_goes_to_a_human_under_one_protocol(graph_for):
    stand_in = model()
    graph = graph_for(stand_in, similarity=0.1)

    paused = to_response(await send(graph), "c1")
    cases = await graph_for.queue.list_open()
    decision = {"action": "reply", "answer": "A antecipação está disponível no app."}
    resumed = to_response(await graph.ainvoke(Command(resume=decision), CONFIG), "c1")
    remembered = (await graph.aget_state(CONFIG)).values["messages"][-1].content

    assert paused.status == "pending_human" and paused.escalated
    assert paused.answer.startswith(SETTLEMENT_A)
    assert paused.escalation.protocol in paused.answer
    assert paused.escalation.reason.value == "low_retrieval_confidence"
    assert paused.sources == [ACCOUNT]
    assert paused.agents == [TargetAgent.SUPPORT, TargetAgent.KNOWLEDGE]
    assert [case.case.protocol for case in cases] == [paused.escalation.protocol]
    attempts = cases[0].case.attempts
    assert any(line.startswith("step 1 (support) answered") for line in attempts)
    assert any(line.startswith("step 2 (knowledge) stopped") for line in attempts)
    assert "ComposedAnswer" not in stand_in.schemas()
    assert resumed.answer == "A antecipação está disponível no app."
    assert resumed.escalation.protocol == paused.escalation.protocol
    assert remembered == f"{SETTLEMENT_A}\n\n{resumed.answer}"


async def test_the_hop_budget_holds_across_the_plan(graph_for):
    handing_back = model(SupportPlan=SupportPlan(action=SupportAction.HANDOFF_TO_KNOWLEDGE))

    final = to_response(await send(graph_for(handing_back)), "c1")

    assert handing_back.schemas().count("RouterDecision") == 2
    assert final.escalation.reason.value == "repeated_misroute"
    assert final.answer.startswith(ADVANCE_A)
    assert final.agents == [TargetAgent.SUPPORT, TargetAgent.KNOWLEDGE]
