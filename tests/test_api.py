"""HTTP integration: the real app, a scripted model and the sampled corpus.

TestClient is used without its context manager on purpose, so the lifespan does not run: the
graph is injected instead of built from a real key and a real index.
"""

import pytest
from fastapi.testclient import TestClient

from app.contracts import Intent, TargetAgent
from app.graph.knowledge import KnowledgeAnswer
from app.graph.router import RouterDecision
from app.graph.support import SupportAction, SupportAnswer, SupportPlan
from app.main import app

CALLER = "cliente1988"


@pytest.fixture
def client(build_test_graph):
    """A client whose graph is scripted per test."""

    def build(responses: dict, **overrides) -> TestClient:
        app.state.graph = build_test_graph(responses, **overrides)
        return TestClient(app)

    return build


def _router(agent: TargetAgent, intent: Intent, confidence: float = 0.95) -> RouterDecision:
    return RouterDecision(
        intent=intent,
        target_agent=agent,
        confidence=confidence,
        rationale="teste",
        lang="en",
        query_pt="em quantas parcelas posso dividir uma venda no crediário",
    )


def test_health_is_served() -> None:
    """Liveness needs no graph at all."""
    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_knowledge_answer_comes_back_with_its_sources(client) -> None:
    """A grounded answer cites the pages it used and is not escalated."""
    http = client(
        {
            RouterDecision: _router(TargetAgent.KNOWLEDGE, Intent.PRODUCT_INFO),
            KnowledgeAnswer: KnowledgeAnswer(answer="Depende do emissor do cartão.", used_urls=[]),
        }
    )

    body = http.post(
        "/chat",
        json={"message": "How many installments with the crediário?", "user_id": CALLER},
    ).json()

    assert body["escalated"] is False
    assert body["target_agent"] == "knowledge"
    assert body["sources"] and body["sources"][0]["url"].startswith("https://")
    assert body["answer"] == "Depende do emissor do cartão."


def test_support_answer_uses_the_tool_result(client) -> None:
    """The account path returns an answer with no corpus sources."""
    http = client(
        {
            RouterDecision: _router(TargetAgent.SUPPORT, Intent.ACCOUNT_DATA),
            SupportPlan: SupportPlan(
                action=SupportAction.CALL_TOOL,
                tool="get_settlement_schedule",
                date_from="2026-09-01",
                date_to="2026-09-30",
            ),
            SupportAnswer: SupportAnswer(answer="A previsão em conta é de R$ 231,26."),
        }
    )

    body = http.post(
        "/chat",
        json={"message": "When will yesterday's sales be deposited?", "user_id": CALLER},
    ).json()

    assert body["escalated"] is False
    assert body["target_agent"] == "support"
    assert body["sources"] == []
    assert "231,26" in body["answer"]


def test_low_router_confidence_escalates_without_naming_an_agent(client) -> None:
    """The confidence edge fires before any specialist, so none may be reported."""
    http = client({RouterDecision: _router(TargetAgent.KNOWLEDGE, Intent.OTHER, confidence=0.2)})

    body = http.post("/chat", json={"message": "hmm, sei lá", "user_id": CALLER}).json()

    assert body["escalated"] is True
    assert body["target_agent"] is None
    assert body["escalation"]["reason"] == "low_router_confidence"
    assert body["escalation"]["protocol"].startswith("ESC-")


def test_guardrail_refusal_is_a_complete_response(client) -> None:
    """Refusing is answering: no escalation, no agent, no sources."""
    http = client({})

    body = http.post(
        "/chat", json={"message": "What's the weather forecast tomorrow?", "user_id": CALLER}
    ).json()

    assert body["escalated"] is False
    assert body["target_agent"] is None
    assert body["sources"] == []
    assert body["answer"].strip()


def test_malformed_payload_is_rejected() -> None:
    """The contract is enforced at the door."""
    assert TestClient(app).post("/chat", json={"message": "", "user_id": ""}).status_code == 422
