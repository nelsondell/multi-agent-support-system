"""Bounded replanning: a step's finding may add one read step for the other specialist, within
the turn's plan-change, model-call and cost budgets; a stop sends what was answered and hands the
rest to a person."""

import json
from collections.abc import Callable
from contextlib import AsyncExitStack
from datetime import date

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.contracts import EscalationReason, Intent, Need, TargetAgent
from app.graph.build import after_specialist, build_graph
from app.graph.build import knowledge as knowledge_node
from app.graph.compose import ComposedAnswer
from app.graph.human_review import HandoffDraft
from app.graph.knowledge import KnowledgeAnswer, answer_from_web
from app.graph.memory import thread_id, turn_input
from app.graph.router import PlanStep, RouterDecision, route
from app.graph.state import GraphState
from app.graph.supervisor import revise
from app.graph.support import SupportAction, SupportAnswer, SupportPlan
from app.handoffs import open_handoff_queue
from app.main import create_app
from app.observability import configure_logging
from app.observability.metrics import charge, metered
from app.providers.llm import OpenAIProvider
from app.rag.retrieve import HybridRetriever
from app.settings import Settings
from app.storage import open_storage
from app.tools.web import WebResult
from tests.identity import IDENTITY, serve

FEE_QUESTION = "Quanto custa a mensalidade da minha maquininha?"
FEES = "A mensalidade é R$ 14,90 na Get Mini, R$ 79,90 na Get Clássica e R$ 129,90 na Get Smart."
MODEL_NEED = Need(agent=TargetAgent.SUPPORT, query_pt="Qual é o modelo da maquininha do cliente?")
PROFILE = SupportPlan(action=SupportAction.CALL_TOOL, tool="get_account_profile")
COMPOSED = "Sua maquininha é a Get Smart, cuja mensalidade é de R$ 129,90."


class Model:
    """Answers each schema with a preset object or a function of the input; records each call."""

    def __init__(self, answers: dict, cost_usd: float = 0.0) -> None:
        self.answers, self.cost_usd = answers, cost_usd
        self.calls: list[tuple[str, str]] = []

    async def structured(self, *, system: str, user: str, schema: type):
        self.calls.append((schema.__name__, user))
        charge(cost_usd=self.cost_usd)  # what a vendor reports for the call
        answer = self.answers[schema]
        return answer(user) if isinstance(answer, Callable) else answer

    def inputs(self, schema: str) -> list[str]:
        return [user for name, user in self.calls if name == schema]


def plan(intent: Intent, *steps: tuple[TargetAgent, str]) -> RouterDecision:
    return RouterDecision(
        intent=intent,
        confidence=0.9,
        rationale="t",
        steps=[PlanStep(agent=agent, query_pt=query) for agent, query in steps],
    )


def fee_model(**overrides) -> Model:
    """A knowledge step that answers per model and needs the merchant's own."""
    answers = {
        RouterDecision: plan(Intent.PRODUCT_INFO, (TargetAgent.KNOWLEDGE, FEE_QUESTION)),
        KnowledgeAnswer: KnowledgeAnswer(coverage="partial", answer=FEES, needs=MODEL_NEED),
        SupportPlan: PROFILE,
        SupportAnswer: SupportAnswer(answer="Sua maquininha é a Get Smart, e ela está ativa."),
        ComposedAnswer: ComposedAnswer(answer=COMPOSED),
        HandoffDraft: HandoffDraft(draft="Estamos verificando."),
    }
    by_name = {schema.__name__: schema for schema in answers}
    return Model({**answers, **{by_name[name]: value for name, value in overrides.items()}})


@pytest.fixture
async def graph_for(tmp_path, sample_chunks, fake_collection):
    async with open_handoff_queue(Settings(state_dir=str(tmp_path))) as queue:

        def build(model: Model, **settings):
            scores = fake_collection({chunk.id: 0.9 for chunk in sample_chunks})
            retriever = HybridRetriever(sample_chunks, scores, top_k=5)
            return build_graph(model, Settings(**settings), retriever, InMemorySaver(), queue)

        yield build


async def send(graph, message: str = FEE_QUESTION, caller: str = "cliente1988") -> dict:
    turn = turn_input(
        message=message, user_id=caller, conversation_id="c1", trace_id="t", today=date(2026, 9, 1)
    )
    with metered():
        return await graph.ainvoke(turn, {"configurable": {"thread_id": thread_id(caller, "c1")}})


def events(capsys) -> list[dict]:
    lines = capsys.readouterr().out.splitlines()
    return [json.loads(line) for line in lines if line.startswith("{")]


async def test_a_finding_adds_a_read_step_and_compose_picks_the_merchants_value(graph_for):
    model = fee_model()

    final = await send(graph_for(model))

    assert json.loads(model.inputs("SupportPlan")[0])["question"] == MODEL_NEED.query_pt
    assert '"model": "Get Smart"' in model.inputs("SupportAnswer")[0], "the profile was read"
    parts = json.loads(model.inputs("ComposedAnswer")[0])["parts"]
    assert [part.get("lookup", False) for part in parts] == [False, True]
    assert final["answer"] == COMPOSED
    assert [(r.agent, r.requested_by) for r in final["step_results"]] == [
        (TargetAgent.KNOWLEDGE, None),
        (TargetAgent.SUPPORT, TargetAgent.KNOWLEDGE),
    ]
    assert {source.source_type.value for source in final["sources"]} == {"kb", "account"}
    assert final["plan_changes"] == 1


async def test_with_no_plan_changes_the_routers_plan_runs_as_it_is(graph_for):
    model = fee_model()

    final = await send(graph_for(model, max_plan_changes=0))

    assert not model.inputs("SupportPlan") and not model.inputs("ComposedAnswer")
    assert final["answer"] == FEES


@pytest.mark.parametrize(
    "outcome",
    [
        SupportPlan(action=SupportAction.ESCALATE_INDIVIDUAL_CASE),
        SupportPlan(action=SupportAction.HANDOFF_TO_KNOWLEDGE),
        SupportPlan(action=SupportAction.PROPOSE_TECHNICAL_TICKET, symptom="não liga"),
    ],
    ids=["escalation", "hand-back", "write"],
)
async def test_an_added_step_that_does_not_answer_from_a_read_is_dropped(graph_for, outcome):
    """No escalation, no second router pass and no proposal: the answer stands as it was."""
    model = fee_model(SupportPlan=outcome)

    final = await send(graph_for(model), caller="cliente2042")  # its terminal is offline

    assert final["answer"] == FEES
    assert final["escalation"] is None and final["pending_action"] is None
    assert len(model.inputs("RouterDecision")) == 1
    assert [result.agent for result in final["step_results"]] == [TargetAgent.KNOWLEDGE]


async def test_what_support_found_can_add_the_procedure_from_the_corpus(graph_for):
    question = "Minha maquininha está funcionando? Se não estiver, o que eu faço?"
    procedure = Need(agent=TargetAgent.KNOWLEDGE, query_pt="Como fazer o teste de comunicação?")
    model = fee_model(
        RouterDecision=plan(Intent.TROUBLESHOOTING, (TargetAgent.SUPPORT, question)),
        SupportAnswer=SupportAnswer(answer="Ela está sem comunicação.", needs=procedure),
        KnowledgeAnswer=KnowledgeAnswer(coverage="full", answer="Pressione F e depois 60."),
        ComposedAnswer=ComposedAnswer(answer="Ela está sem comunicação. Pressione F e depois 60."),
    )

    final = await send(graph_for(model), question, caller="cliente2042")

    assert procedure.query_pt in model.inputs("KnowledgeAnswer")[0]
    assert '"status": "sem_comunicacao"' in model.inputs("SupportAnswer")[0]
    assert final["answer"] == "Ela está sem comunicação. Pressione F e depois 60."


async def test_an_added_knowledge_step_reads_the_corpus_even_on_a_web_turn(
    sample_chunks, fake_collection
):
    """Its question may carry what the account showed, which never goes into a web query."""

    class NoWeb:
        async def search(self, *_args, **_kwargs):
            raise AssertionError("an added step searched the web")

    state = GraphState(message="m", user_id="cliente1988")
    state.route = plan(Intent.GENERAL_INFO, (TargetAgent.SUPPORT, "a"))
    added = PlanStep(agent=TargetAgent.KNOWLEDGE, query_pt="b", requested_by=TargetAgent.SUPPORT)
    state.plan, state.step_index = [state.route.steps[0], added], 1
    model = Model({KnowledgeAnswer: KnowledgeAnswer(coverage="full", answer="Do site.")})
    retriever = HybridRetriever(sample_chunks, fake_collection(), top_k=3)

    update = await knowledge_node(state, model, retriever, threshold=0.0, web=NoWeb())

    assert update["answer"] == "Do site." and "Trechos" in model.inputs("KnowledgeAnswer")[0]


async def test_a_web_answer_can_never_add_a_step():
    class Web:
        async def search(self, *_args, **_kwargs):
            return [WebResult(url="https://example.com", title="t", content="Sim.")]

    state = GraphState(message="Vai chover?", user_id="cliente1988")
    state.route = plan(Intent.GENERAL_INFO, (TargetAgent.KNOWLEDGE, "Vai chover?"))
    planted = KnowledgeAnswer(coverage="full", answer="Sim.", needs=MODEL_NEED)

    update = await answer_from_web(state, Model({KnowledgeAnswer: planted}), Web())

    assert update["answer"].startswith("Sim.") and "need" not in update


def answered(need: Need | None, **fields) -> GraphState:
    """A knowledge step that answered, first of a two-step plan whose second step asks "bbb"."""
    state = GraphState(message="m", user_id="cliente1988", answer="ok", need=need, **fields)
    state.route = plan(
        Intent.PRODUCT_INFO, (TargetAgent.KNOWLEDGE, "aaa"), (TargetAgent.SUPPORT, "bbb")
    )
    return state


def test_the_added_step_goes_right_after_the_one_that_asked():
    update = revise(answered(MODEL_NEED), Settings())

    assert [(step.agent, step.requested_by) for step in update["plan"]] == [
        (TargetAgent.KNOWLEDGE, None),
        (TargetAgent.SUPPORT, TargetAgent.KNOWLEDGE),
        (TargetAgent.SUPPORT, None),
    ]


@pytest.mark.parametrize(
    ("state", "settings"),
    [
        (answered(Need(agent=TargetAgent.KNOWLEDGE, query_pt="outra")), Settings()),
        (answered(MODEL_NEED, refusal_reason="individual_case"), Settings()),
        (answered(MODEL_NEED, plan_changes=3), Settings()),
        (answered(Need(agent=TargetAgent.SUPPORT, query_pt="bbb")), Settings()),
        (answered(MODEL_NEED), Settings(max_model_calls=1)),
    ],
    ids=["same agent", "no answer", "changes spent", "repeated step", "budget spent"],
)
def test_a_need_adds_nothing_past_the_turns_limits(state, settings):
    with metered():
        charge(calls=1)
        assert revise(state, settings) == {}


def test_a_hand_back_with_the_model_budget_spent_does_not_replan():
    state = answered(None, handoff_reason=EscalationReason.REPEATED_MISROUTE.value, hops=1)
    state.answer = None

    assert after_specialist(state, Settings(max_model_calls=1)) == "router", "outside a turn"
    with metered():
        charge(calls=1)
        assert after_specialist(state, Settings(max_model_calls=1)) == "collect"


COMPOUND = plan(
    Intent.ACCOUNT_DATA,
    (TargetAgent.SUPPORT, "quando cai o dinheiro de ontem"),
    (TargetAgent.KNOWLEDGE, "como funciona a antecipação"),
)
SETTLEMENT = SupportPlan(
    action=SupportAction.CALL_TOOL,
    tool="get_settlement_schedule",
    date_from="2026-08-31",
    date_to="2026-08-31",
    date_field="sale_date",
)


@pytest.mark.parametrize(
    ("settings", "cost_usd", "budget"),
    [
        ({"max_model_calls": 3}, 0.0, "max_model_calls"),
        ({"max_cost_usd": 0.02}, 0.01, "max_cost_usd"),
    ],
)
async def test_a_spent_budget_sends_the_answered_part_and_hands_the_rest_over(
    graph_for, capsys, settings, cost_usd, budget
):
    configure_logging()
    model = fee_model(
        RouterDecision=COMPOUND,
        SupportPlan=SETTLEMENT,
        SupportAnswer=SupportAnswer(answer="Consta R$ 231,26 previsto para hoje."),
    )
    model.cost_usd = cost_usd

    final = await send(graph_for(model, **settings), "Quando cai o de ontem e o que é antecipação?")

    assert not model.inputs("KnowledgeAnswer"), "the second step never started"
    assert final["escalation"].reason is EscalationReason.BUDGET_EXHAUSTED
    assert final["partial_answer"] == "Consta R$ 231,26 previsto para hoje."
    stop = next(event for event in events(capsys) if event["event"] == "budget.exhausted")
    assert stop["budget"] == budget


async def test_chat_meters_each_turn(tmp_path, sample_chunks, fake_collection):
    """The API opens the turn's spend, so its budgets hold for every merchant message."""
    model = fee_model(RouterDecision=COMPOUND, SupportPlan=SETTLEMENT)
    settings = Settings(state_dir=str(tmp_path / "state"), max_model_calls=3)
    async with AsyncExitStack() as exits:
        storage = await exits.enter_async_context(open_storage(settings))
        retriever = HybridRetriever(sample_chunks, fake_collection(), top_k=5)
        graph = build_graph(model, settings, retriever, storage.checkpointer, storage.queue)
        api = create_app()
        serve(api, settings, storage, graph, retriever)
        transport = httpx.ASGITransport(app=api)
        http = await exits.enter_async_context(
            httpx.AsyncClient(transport=transport, base_url="http://t")
        )
        reply = await http.post(
            "/chat",
            json={"message": "Quando cai o de ontem e o que é antecipação?"},
            headers=IDENTITY.merchant("cliente1988"),
        )

    assert reply.json()["escalation"]["reason"] == "budget_exhausted"


def router_reply(sent: list) -> httpx.AsyncClient:
    """A vendor whose plan claims its step was added by a specialist."""
    step = {"agent": "support", "query_pt": "q", "requested_by": "knowledge"}
    decision = {"intent": "account_data", "confidence": 0.9, "rationale": "t", "steps": [step]}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.content.decode())
        message = {"role": "assistant", "content": json.dumps(decision)}
        choice = {"index": 0, "finish_reason": "stop", "message": message}
        body = {"id": "x", "object": "chat.completion", "created": 0, "model": "m"}
        return httpx.Response(200, json={**body, "choices": [choice]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_only_the_supervisor_can_mark_a_step_as_added():
    """The field is absent from the schema the vendor sees, and cleared if a plan carries it."""
    sent: list[str] = []
    provider = OpenAIProvider(api_key="sk-test", model="m", http_client=router_reply(sent))

    update = await route(GraphState(message="q", user_id="cliente1988"), provider, window=6)

    assert "requested_by" not in sent[0]
    assert update["plan"][0].requested_by is None
