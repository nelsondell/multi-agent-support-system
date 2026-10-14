"""v2 flows end to end over HTTP: memory, restart, isolation, plans, web search and tool failure.

The real app runs over SQLite stores in a temporary directory, with a stand-in model routed by
keyword and, where a test needs it, a web client over a mocked transport. Assertions check the
invariants the spec states, never a model's wording. The human-review loop over HTTP lives in
`test_human_review`.
"""

import json
from collections.abc import Callable
from contextlib import AsyncExitStack

import httpx
import pytest

from app.checkpoints import open_checkpointer
from app.contracts import Intent, SourceType, TargetAgent
from app.graph.build import build_graph
from app.graph.compose import ComposedAnswer
from app.graph.human_review import HandoffDraft
from app.graph.knowledge import KnowledgeAnswer
from app.graph.router import PlanStep, RouterDecision
from app.graph.support import SupportAction, SupportAnswer, SupportPlan
from app.handoffs import open_handoff_queue
from app.main import app
from app.rag.retrieve import HybridRetriever
from app.settings import Settings

CALLER = "cliente1988"
STRANGER = "cliente7771"
SETTLED = "O dinheiro das vendas de 31/08/2026, R$ 231,26 líquidos, está previsto para 01/09/2026."
PIX = "A Get Smart aceita Pix."
WEATHER = {
    "url": "https://clima.example/porto-alegre",
    "title": "Previsão do tempo em Porto Alegre",
    "content": "Amanhã em Porto Alegre: máxima de 24°C e mínima de 15°C.",
}


def plan(intent: Intent, *steps: tuple[TargetAgent, str]) -> RouterDecision:
    return RouterDecision(
        intent=intent,
        confidence=0.9,
        rationale="test",
        steps=[PlanStep(agent=agent, query_pt=query) for agent, query in steps],
    )


def routed(user: str) -> RouterDecision:
    """By keyword: both parts, the account, the web, or the corpus."""
    message = json.loads(user)["message"].lower()
    if "antecipação" in message and "dinheiro" in message:
        return plan(
            Intent.ACCOUNT_DATA,
            (TargetAgent.SUPPORT, "quando cai o dinheiro das vendas de ontem"),
            (TargetAgent.KNOWLEDGE, "como funciona a antecipação"),
        )
    if "tempo" in message:
        return plan(Intent.GENERAL_INFO, (TargetAgent.KNOWLEDGE, message))
    if "dinheiro" in message or "amanhã?" in message:
        return plan(Intent.ACCOUNT_DATA, (TargetAgent.SUPPORT, message))
    return plan(Intent.PRODUCT_INFO, (TargetAgent.KNOWLEDGE, message))


class Model:
    """Answers each schema with a preset object or a function of the input; records each call."""

    def __init__(self, **overrides) -> None:
        answers = {
            RouterDecision: routed,
            SupportPlan: SupportPlan(
                action=SupportAction.CALL_TOOL,
                tool="get_settlement_schedule",
                date_from="2026-08-31",
                date_to="2026-08-31",
                date_field="sale_date",
            ),
            SupportAnswer: SupportAnswer(answer=SETTLED),
            KnowledgeAnswer: KnowledgeAnswer(coverage="full", answer=PIX, used_urls=[]),
            ComposedAnswer: ComposedAnswer(answer=f"{SETTLED} {PIX}"),
            HandoffDraft: HandoffDraft(draft="Estamos verificando a sua conta."),
        }
        by_name = {schema.__name__: schema for schema in answers}
        self.answers = {**answers, **{by_name[name]: value for name, value in overrides.items()}}
        self.calls: list[tuple[str, str]] = []

    async def structured(self, *, system: str, user: str, schema: type):
        self.calls.append((schema.__name__, user))
        answer = self.answers[schema]
        return answer(user) if isinstance(answer, Callable) else answer

    def router_inputs(self) -> list[dict]:
        return [json.loads(user) for name, user in self.calls if name == "RouterDecision"]


@pytest.fixture
def stack(tmp_path, sample_chunks, fake_collection):
    """Start the app over stores in `tmp_path`; starting again reopens the same files."""

    async def start(exits: AsyncExitStack, model: Model, web=None) -> httpx.AsyncClient:
        settings = Settings(state_dir=str(tmp_path / "state"))
        checkpointer = await exits.enter_async_context(open_checkpointer(settings))
        queue = await exits.enter_async_context(open_handoff_queue(settings))
        retriever = HybridRetriever(
            sample_chunks, fake_collection({chunk.id: 0.9 for chunk in sample_chunks}), top_k=5
        )
        app.state.settings, app.state.queue = settings, queue
        app.state.graph = build_graph(model, settings, retriever, checkpointer, queue, web)
        transport = httpx.ASGITransport(app=app)
        return await exits.enter_async_context(
            httpx.AsyncClient(transport=transport, base_url="http://test")
        )

    return start


async def chat(http, message: str, conversation_id: str = "c1", user_id: str = CALLER) -> dict:
    payload = {"message": message, "user_id": user_id, "conversation_id": conversation_id}
    response = await http.post("/chat", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_second_turn_reads_the_first(stack):
    model = Model()
    async with AsyncExitStack() as exits:
        http = await stack(exits, model)
        first = await chat(http, "Quando cai o dinheiro das vendas de ontem?")
        second = await chat(http, "E o de amanhã?")

    history = model.router_inputs()[1]["history"]
    assert first["conversation_id"] == second["conversation_id"] == "c1"
    assert [item["content"] for item in history] == [
        "Quando cai o dinheiro das vendas de ontem?",
        SETTLED,
    ]
    assert "history" not in model.router_inputs()[0], "a first turn carries no history block"


async def test_a_restart_over_the_same_state_directory_continues_the_conversation(stack):
    model = Model()
    async with AsyncExitStack() as exits:
        await chat(await stack(exits, model), "Quando cai o dinheiro das vendas de ontem?")
    async with AsyncExitStack() as exits:
        await chat(await stack(exits, model), "E o de amanhã?")

    assert model.router_inputs()[1]["history"][0]["content"].startswith("Quando cai o dinheiro")


async def test_the_same_conversation_id_under_another_merchant_starts_empty(stack):
    model = Model()
    async with AsyncExitStack() as exits:
        http = await stack(exits, model)
        await chat(http, "Quando cai o dinheiro das vendas de ontem?")
        await chat(http, "A Get Smart aceita Pix?", user_id=STRANGER)

    assert "history" not in model.router_inputs()[1]


async def test_a_compound_request_answers_both_parts_with_every_source(stack):
    async with AsyncExitStack() as exits:
        http = await stack(exits, Model())
        reply = await chat(http, "Quando cai o dinheiro de ontem e como funciona a antecipação?")

    kinds = [source["source_type"] for source in reply["sources"]]
    assert reply["status"] == "answered" and not reply["escalated"]
    assert reply["agents"] == ["support", "knowledge"]
    assert kinds[0] == SourceType.ACCOUNT and SourceType.KB in kinds


async def test_a_general_question_is_answered_from_the_web_with_a_key(stack, web_search):
    web, sent = web_search([WEATHER])
    reply_text = "Amanhã a máxima em Porto Alegre é de 24°C."
    model = Model(
        KnowledgeAnswer=KnowledgeAnswer(
            coverage="full", answer=reply_text, used_urls=[WEATHER["url"]]
        )
    )
    async with AsyncExitStack() as exits:
        http = await stack(exits, model, web)
        reply = await chat(http, "Como vai estar o tempo em Porto Alegre amanhã?")

    assert reply["answer"] == reply_text
    assert reply["sources"] == [
        {"source_type": "web", "url": WEATHER["url"], "title": WEATHER["title"]}
    ]
    assert len(sent) == 1


async def test_without_a_key_a_general_question_degrades_and_nothing_leaves(stack, web_search):
    web, sent = web_search([WEATHER], key=None)
    async with AsyncExitStack() as exits:
        http = await stack(exits, Model(), web)
        reply = await chat(http, "Como vai estar o tempo em Porto Alegre amanhã?")

    assert reply["status"] == "answered" and not reply["escalated"]
    assert reply["sources"] == []
    assert sent == []


async def test_the_web_query_that_leaves_is_masked(stack, web_search):
    web, sent = web_search([WEATHER])
    async with AsyncExitStack() as exits:
        http = await stack(exits, Model(), web)
        await chat(http, "Meu CPF é 123.456.789-00, como vai estar o tempo amanhã?")

    assert "[CPF]" in sent[0]["query"]
    assert not any(character.isdigit() for character in sent[0]["query"])


async def test_an_unreadable_account_escalates_instead_of_failing_the_request(stack, monkeypatch):
    """A tool error becomes an escalation with its own reason, never a stack trace."""

    def unreadable():
        raise FileNotFoundError("customer records not found")

    monkeypatch.setattr("app.tools.account._customers", unreadable)
    async with AsyncExitStack() as exits:
        http = await stack(exits, Model())
        reply = await chat(http, "Quando cai o dinheiro das vendas de ontem?")

    assert reply["status"] == "pending_human"
    assert reply["escalation"]["reason"] == "tool_unavailable"
