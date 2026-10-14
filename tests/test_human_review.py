"""Human in the loop, end to end over HTTP: agent → human → agent continues.

The real app runs over a SQLite checkpointer and handoff queue in a temporary directory, with a
stand-in model that records every call. The operator endpoints are driven exactly as an operator
would drive them.
"""

import json
from collections.abc import Callable
from contextlib import AsyncExitStack

import httpx
import pytest

from app.checkpoints import open_checkpointer
from app.contracts import Intent, TargetAgent
from app.graph.build import build_graph
from app.graph.human_review import HandoffDraft, OperatorReply
from app.graph.knowledge import KnowledgeAnswer
from app.graph.memory import thread_id
from app.graph.router import PlanStep, RouterDecision
from app.graph.support import SupportAction, SupportAnswer, SupportPlan
from app.handoffs import open_handoff_queue
from app.main import app
from app.providers.llm import LLMProviderError
from app.rag.retrieve import HybridRetriever
from app.settings import Settings

CALLER = "cliente1988"
TOKEN = "operator-test-token"
OPERATOR = {"Authorization": f"Bearer {TOKEN}"}
COMPLAINT = "Fui cobrado duas vezes na mesma venda, meu CPF é 123.456.789-00"
DRAFT = "Recebemos sua solicitação e nossa equipe está verificando a cobrança."
PROTOCOL = "ESC-3B500C0D1E"
"""Pinned so the tests are deterministic, and full of loose digits on purpose: a protocol must
never let a "3" or a "500" through hard rule 4 just by containing them."""


class Model:
    """Answers each schema with a preset object, or a function of the input; records each call."""

    def __init__(self, answers: dict) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str]] = []

    async def structured(self, *, system: str, user: str, schema: type):
        self.calls.append((schema.__name__, user))
        answer = self.answers[schema]
        return answer(user) if isinstance(answer, Callable) else answer


def routed(user: str) -> RouterDecision:
    """Complaints go to support as individual cases; product questions to knowledge."""
    message = json.loads(user)["message"]
    product = "Get Smart" in message
    return RouterDecision(
        intent=Intent.PRODUCT_INFO if product else Intent.INDIVIDUAL_CASE,
        steps=[
            PlanStep(
                agent=TargetAgent.KNOWLEDGE if product else TargetAgent.SUPPORT, query_pt=message
            )
        ],
        confidence=0.9,
        rationale="test",
    )


def model(**overrides) -> Model:
    answers = {
        RouterDecision: routed,
        SupportPlan: SupportPlan(action=SupportAction.ESCALATE_INDIVIDUAL_CASE),
        SupportAnswer: SupportAnswer(answer="Forecast found in the account."),
        KnowledgeAnswer: KnowledgeAnswer(
            coverage="full", answer="It takes cards and Pix.", used_urls=[]
        ),
        HandoffDraft: HandoffDraft(draft=DRAFT),
        OperatorReply: lambda user: OperatorReply(answer=json.loads(user)["operator_answer"]),
    }
    by_name = {schema.__name__: schema for schema in answers}
    return Model({**answers, **{by_name[name]: value for name, value in overrides.items()}})


@pytest.fixture(autouse=True)
def pinned_protocol(monkeypatch):
    monkeypatch.setattr("app.graph.escalation.mint_protocol", lambda: PROTOCOL)


@pytest.fixture
def stack(tmp_path, sample_chunks, fake_collection):
    """Start the app over stores in `tmp_path`; starting again reopens the same files."""

    async def start(exits: AsyncExitStack, stand_in: Model, token: str | None = TOKEN):
        settings = Settings(state_dir=str(tmp_path / "state"), operator_token=token)
        checkpointer = await exits.enter_async_context(open_checkpointer(settings))
        queue = await exits.enter_async_context(open_handoff_queue(settings))
        above = fake_collection({chunk.id: 0.9 for chunk in sample_chunks})
        retriever = HybridRetriever(sample_chunks, above, top_k=5)
        app.state.settings, app.state.queue = settings, queue
        app.state.graph = build_graph(stand_in, settings, retriever, checkpointer, queue)
        transport = httpx.ASGITransport(app=app)
        return await exits.enter_async_context(
            httpx.AsyncClient(transport=transport, base_url="http://test")
        )

    return start


async def chat(http: httpx.AsyncClient, message: str, conversation_id: str = "c1") -> dict:
    payload = {"message": message, "user_id": CALLER, "conversation_id": conversation_id}
    response = await http.post("/chat", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def history(conversation_id: str = "c1") -> list[str]:
    config = {"configurable": {"thread_id": thread_id(CALLER, conversation_id)}}
    snapshot = await app.state.graph.aget_state(config)
    return [message.content for message in snapshot.values.get("messages", [])]


async def test_the_full_loop_from_pause_to_the_next_turn(stack):
    stand_in = model()
    async with AsyncExitStack() as exits:
        http = await stack(exits, stand_in)

        paused = await chat(http, COMPLAINT)
        protocol = paused["escalation"]["protocol"]
        again = await chat(http, "Alguma novidade?")
        listed = (await http.get("/handoffs", headers=OPERATOR)).json()
        decision = {"action": "reply", "answer": "Estorno aprovado, cai em 2 dias úteis."}
        resumed = (
            await http.post(f"/handoffs/{protocol}/resume", json=decision, headers=OPERATOR)
        ).json()
        remembered = await history()
        await chat(http, "E quando cai mesmo?")
        router_inputs = [user for schema, user in stand_in.calls if schema == "RouterDecision"]
        next_router_input = json.loads(router_inputs[-1])

    assert paused["status"] == "pending_human" and paused["sources"] == []
    assert paused["escalation"]["reason"] == "individual_case"
    assert again["status"] == "pending_human" and protocol in again["answer"]
    assert [record["case"]["protocol"] for record in listed] == [protocol]
    case = listed[0]["case"]
    assert "[CPF]" in case["summary"] and "123.456.789-00" not in case["summary"]
    assert case["intent"] == "individual_case" and case["attempts"]
    assert case["proposed_draft"] == DRAFT and case["conversation_id"] == "c1"
    assert resumed["status"] == "answered" and resumed["escalated"] is True
    assert resumed["answer"] == "Estorno aprovado, cai em 2 dias úteis."
    assert resumed["sources"] == [{"url": None, "title": protocol, "source_type": "operator"}]
    assert remembered[-1] == resumed["answer"]
    assert next_router_input["history"][-1]["content"] == resumed["answer"]


async def test_a_pending_conversation_starts_no_new_run(stack):
    stand_in = model()
    async with AsyncExitStack() as exits:
        http = await stack(exits, stand_in)
        paused = await chat(http, COMPLAINT)
        calls = len(stand_in.calls)

        again = await chat(http, "Alguma novidade?")

    assert again["status"] == "pending_human"
    assert again["escalation"]["protocol"] == paused["escalation"]["protocol"]
    assert len(stand_in.calls) == calls


async def test_other_conversations_are_unaffected(stack):
    async with AsyncExitStack() as exits:
        http = await stack(exits, model())
        await chat(http, COMPLAINT, conversation_id="c1")

        other = await chat(http, "O que é a Get Smart?", conversation_id="c2")

    assert other["status"] == "answered" and other["escalated"] is False


async def test_approve_sends_the_proposed_draft(stack):
    async with AsyncExitStack() as exits:
        http = await stack(exits, model())
        protocol = (await chat(http, COMPLAINT))["escalation"]["protocol"]

        resumed = await http.post(
            f"/handoffs/{protocol}/resume", json={"action": "approve"}, headers=OPERATOR
        )

    assert resumed.status_code == 200
    assert resumed.json()["answer"] == DRAFT


def answer_step_down(user: str) -> SupportAnswer:
    raise LLMProviderError("answer model unavailable")


@pytest.mark.parametrize(
    ("draft", "kept"),
    [
        ("Consta na sua agenda uma previsão de R$ 231,26. Nossa equipe vai verificar.", True),
        ("Consta na sua agenda uma previsão de R$ 500,00. Nossa equipe vai verificar.", False),
    ],
)
async def test_a_draft_may_quote_the_tool_result_but_nothing_else(stack, draft, kept):
    """The settlement row was read, then the answer step failed: the case goes to a human."""
    looked_up = model(
        SupportPlan=SupportPlan(
            action=SupportAction.CALL_TOOL,
            tool="get_settlement_schedule",
            date_from="2026-09-01",
            date_to="2026-09-01",
        ),
        SupportAnswer=answer_step_down,
        HandoffDraft=HandoffDraft(draft=draft),
    )
    async with AsyncExitStack() as exits:
        http = await stack(exits, looked_up)
        protocol = (await chat(http, COMPLAINT))["escalation"]["protocol"]

        case = (await http.get(f"/handoffs/{protocol}", headers=OPERATOR)).json()["case"]

    assert case["reason"] == "agent_not_implemented"
    assert case["proposed_draft"] == (draft if kept else None)


async def test_a_rephrasing_may_quote_the_protocol_verbatim(stack):
    quoting = model(
        OperatorReply=OperatorReply(
            answer=f"Protocolo {PROTOCOL}: estorno aprovado em 2 dias úteis."
        )
    )
    async with AsyncExitStack() as exits:
        http = await stack(exits, quoting)
        await chat(http, COMPLAINT)
        decision = {"action": "reply", "answer": "Estorno aprovado, cai em 2 dias úteis."}

        resumed = await http.post(f"/handoffs/{PROTOCOL}/resume", json=decision, headers=OPERATOR)

    assert resumed.json()["answer"] == f"Protocolo {PROTOCOL}: estorno aprovado em 2 dias úteis."


async def test_a_rephrasing_with_a_new_number_falls_back_to_the_operator_text(stack):
    inventive = model(OperatorReply=OperatorReply(answer="Estorno aprovado, cai em 3 dias úteis."))
    async with AsyncExitStack() as exits:
        http = await stack(exits, inventive)
        protocol = (await chat(http, COMPLAINT))["escalation"]["protocol"]
        decision = {"action": "reply", "answer": "Estorno aprovado, cai em 2 dias úteis."}

        resumed = await http.post(f"/handoffs/{protocol}/resume", json=decision, headers=OPERATOR)

    assert resumed.json()["answer"] == "Estorno aprovado, cai em 2 dias úteis."


async def test_a_rephrasing_that_only_respells_a_value_is_kept(stack):
    respelled = model(OperatorReply=OperatorReply(answer="O estorno de 1234.56 foi aprovado."))
    async with AsyncExitStack() as exits:
        http = await stack(exits, respelled)
        protocol = (await chat(http, COMPLAINT))["escalation"]["protocol"]
        decision = {"action": "reply", "answer": "Estorno de R$ 1.234,56 aprovado."}

        resumed = await http.post(f"/handoffs/{protocol}/resume", json=decision, headers=OPERATOR)

    assert resumed.json()["answer"] == "O estorno de 1234.56 foi aprovado."


async def test_a_pending_case_survives_a_rebuild(stack):
    async with AsyncExitStack() as exits:
        http = await stack(exits, model())
        protocol = (await chat(http, COMPLAINT))["escalation"]["protocol"]

    async with AsyncExitStack() as exits:
        http = await stack(exits, model())
        again = await chat(http, "Alguma novidade?")
        listed = (await http.get("/handoffs", headers=OPERATOR)).json()
        decision = {"action": "reply", "answer": "Estorno aprovado."}
        resumed = await http.post(f"/handoffs/{protocol}/resume", json=decision, headers=OPERATOR)
        remembered = await history()

    assert again["status"] == "pending_human"
    assert [record["case"]["protocol"] for record in listed] == [protocol]
    assert resumed.status_code == 200
    assert remembered == [COMPLAINT.replace("123.456.789-00", "[CPF]"), "Estorno aprovado."]


async def test_one_case_keeps_one_protocol_through_pause_list_and_resume(stack):
    async with AsyncExitStack() as exits:
        http = await stack(exits, model())
        protocol = (await chat(http, COMPLAINT))["escalation"]["protocol"]
        shown = (await http.get(f"/handoffs/{protocol}", headers=OPERATOR)).json()
        resumed = (
            await http.post(
                f"/handoffs/{protocol}/resume", json={"action": "approve"}, headers=OPERATOR
            )
        ).json()
        closed = (await http.get(f"/handoffs/{protocol}", headers=OPERATOR)).json()

    assert shown["status"] == "open" and shown["case"]["protocol"] == protocol
    assert resumed["escalation"]["protocol"] == protocol
    assert closed["status"] == "closed" and closed["closed_at"]


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}]
)
async def test_operator_routes_reject_a_missing_or_wrong_token(stack, headers):
    async with AsyncExitStack() as exits:
        http = await stack(exits, model())

        listed = await http.get("/handoffs", headers=headers)
        resumed = await http.post(
            "/handoffs/ESC-X/resume", json={"action": "approve"}, headers=headers
        )

    assert listed.status_code == resumed.status_code == 401


async def test_operator_routes_are_disabled_without_a_configured_token(stack):
    async with AsyncExitStack() as exits:
        http = await stack(exits, model(), token=None)

        response = await http.get("/handoffs", headers=OPERATOR)

    assert response.status_code == 503


async def test_an_unknown_protocol_is_404(stack):
    async with AsyncExitStack() as exits:
        http = await stack(exits, model())

        shown = await http.get("/handoffs/ESC-0000000000", headers=OPERATOR)
        resumed = await http.post(
            "/handoffs/ESC-0000000000/resume", json={"action": "approve"}, headers=OPERATOR
        )

    assert shown.status_code == resumed.status_code == 404


async def test_resuming_a_closed_case_is_409(stack):
    async with AsyncExitStack() as exits:
        http = await stack(exits, model())
        protocol = (await chat(http, COMPLAINT))["escalation"]["protocol"]
        route = f"/handoffs/{protocol}/resume"
        await http.post(route, json={"action": "approve"}, headers=OPERATOR)

        second = await http.post(route, json={"action": "approve"}, headers=OPERATOR)

    assert second.status_code == 409


async def test_a_product_question_the_excerpts_do_not_answer_goes_to_a_human(stack):
    """The model's "not found" never reaches the merchant as an answer; a protocol does."""
    unanswered = KnowledgeAnswer(
        coverage="none", answer="Não encontrei isso nos trechos.", used_urls=[]
    )
    async with AsyncExitStack() as exits:
        http = await stack(exits, model(KnowledgeAnswer=unanswered))
        paused = await chat(http, "A Get Smart funciona sem internet?")
        listed = (await http.get("/handoffs", headers=OPERATOR)).json()

    assert paused["status"] == "pending_human"
    assert paused["escalation"]["reason"] == "insufficient_sources"
    assert PROTOCOL in paused["answer"]
    assert "Não encontrei isso nos trechos" not in paused["answer"]
    assert [case["case"]["protocol"] for case in listed] == [PROTOCOL]
