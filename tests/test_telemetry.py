"""Metrics and optional tracing: counted in one place, served by /metrics, masked when on, and
never able to fail a turn."""

import langsmith
import pytest
from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda
from langchain_core.tracers.langchain import wait_for_all_tracers
from langgraph.checkpoint.memory import InMemorySaver
from prometheus_client import REGISTRY

from app.contracts import Intent, TargetAgent
from app.graph.human_review import HandoffDraft
from app.graph.knowledge import KnowledgeAnswer
from app.graph.memory import thread_id, turn_input
from app.graph.router import PlanStep, RouterDecision
from app.main import app
from app.observability import tracing
from app.observability.tracing import configure_tracing
from app.settings import Settings

CPF = "123.456.789-00"


def decision(confidence: float) -> RouterDecision:
    return RouterDecision(
        intent=Intent.PRODUCT_INFO,
        confidence=confidence,
        rationale="test",
        steps=[PlanStep(agent=TargetAgent.KNOWLEDGE, query_pt="como funciona a antecipação")],
    )


async def run_turn(graph, message: str = "Como funciona a antecipação?") -> dict:
    turn = turn_input(
        message=message, user_id="cliente1988", conversation_id="c1", trace_id="t", today=None
    )
    return await graph.ainvoke(
        turn, {"configurable": {"thread_id": thread_id("cliente1988", "c1")}}
    )


def sample(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_an_escalation_increments_the_counter_of_its_reason(build_test_graph):
    graph = build_test_graph(
        {RouterDecision: decision(0.1), HandoffDraft: HandoffDraft(draft="Estamos verificando.")},
        checkpointer=InMemorySaver(),
    )
    labels = {"reason": "low_router_confidence"}
    before = sample("support_escalations_total", labels)

    await run_turn(graph)

    assert sample("support_escalations_total", labels) == before + 1


async def test_every_node_run_is_timed_under_its_name(build_test_graph):
    answered = KnowledgeAnswer(coverage="full", answer="Adianta os recebíveis.", used_urls=[])
    graph = build_test_graph({RouterDecision: decision(0.9), KnowledgeAnswer: answered})
    before = sample("support_node_latency_seconds_count", {"node": "knowledge"})

    await run_turn(graph)

    assert sample("support_node_latency_seconds_count", {"node": "knowledge"}) == before + 1


def test_metrics_exposes_both_families() -> None:
    response = TestClient(app).get("/metrics")

    assert response.status_code == 200
    assert "# TYPE support_escalations_total counter" in response.text
    assert "# TYPE support_node_latency_seconds histogram" in response.text


class Recorder(langsmith.Client):
    """A LangSmith client that keeps what it would upload, after its own masking ran."""

    uploads: list = []
    broken = False

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs, auto_batch_tracing=False)

    def _create_run(self, run_create, **kwargs):
        if self.broken:
            raise ConnectionError("langsmith unreachable")
        Recorder.uploads.append(run_create.get("inputs"))

    def _update_run(self, run_update, **kwargs):
        """A run's end is an upload too; it stays here, so the suite opens no socket."""
        if self.broken:
            raise ConnectionError("langsmith unreachable")


@pytest.fixture
def recorder(monkeypatch):
    Recorder.uploads, Recorder.broken = [], False
    monkeypatch.setattr(tracing.langsmith, "Client", Recorder)
    yield Recorder
    langsmith.configure(client=None, enabled=False)


def tracing_on() -> Settings:
    return Settings(langsmith_tracing=True, langsmith_api_key="lsv2-test")


def test_a_cpf_is_masked_before_upload(recorder):
    assert configure_tracing(tracing_on()) is True

    RunnableLambda(lambda payload: payload).invoke({"message": f"meu CPF é {CPF}"})
    wait_for_all_tracers()

    assert recorder.uploads, "the run was traced"
    assert recorder.uploads[0] == {"message": "meu CPF é [CPF]"}


def test_off_means_off_even_when_the_environment_asks_for_tracing(recorder, monkeypatch):
    """LangChain would start its own unmasked tracer from LANGSMITH_TRACING=true."""
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")

    assert configure_tracing(Settings(langsmith_tracing=False)) is False
    RunnableLambda(lambda payload: payload).invoke({"message": f"meu CPF é {CPF}"})
    wait_for_all_tracers()

    assert recorder.uploads == []
    assert langsmith.utils.tracing_is_enabled() is False


def test_on_without_a_key_stays_off(recorder):
    assert configure_tracing(Settings(langsmith_tracing=True)) is False


async def test_a_failing_tracer_never_fails_a_turn(recorder, build_test_graph):
    recorder.broken = True
    configure_tracing(tracing_on())
    answered = KnowledgeAnswer(coverage="full", answer="Adianta os recebíveis.", used_urls=[])
    graph = build_test_graph({RouterDecision: decision(0.9), KnowledgeAnswer: answered})

    final = await run_turn(graph)
    wait_for_all_tracers()

    assert final["answer"] == "Adianta os recebíveis."
