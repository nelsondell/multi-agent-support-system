"""Traces, cost and vendor failures.

A turn is one trace: the HTTP span, an `invoke_agent` span per node and an `execute_tool` span per
tool call, whose trace id is the `trace_id` of the turn's logs. A model call records tokens,
cache reads and cost, never content, and sends its static prompt as a cache breakpoint. Turn
outcomes and vendor failures are counted, including the faults the drills inject.
"""

import json
from contextlib import AsyncExitStack

import httpx
import pytest
import structlog
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY
from pydantic import BaseModel

from app.contracts import Intent, TargetAgent
from app.graph.build import build_graph
from app.graph.human_review import HandoffDraft
from app.graph.router import PlanStep, RouterDecision
from app.graph.support import SupportAction, SupportAnswer, SupportPlan
from app.main import create_app
from app.providers.llm import FaultyProvider, OpenAIProvider
from app.rag.retrieve import HybridRetriever
from app.settings import Settings
from app.storage import open_storage
from app.tools.web import WebSearch, WebSearchUnavailable
from tests.identity import IDENTITY, serve

EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def tracer_provider():
    """The SDK provider, installed once per process as OpenTelemetry requires."""
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
    trace.set_tracer_provider(provider)


@pytest.fixture(autouse=True)
def spans():
    EXPORTER.clear()
    return EXPORTER


def sample(name: str, labels: dict | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


ACCOUNT = {
    RouterDecision: RouterDecision(
        intent=Intent.ACCOUNT_DATA,
        confidence=0.9,
        rationale="t",
        steps=[PlanStep(agent=TargetAgent.SUPPORT, query_pt="quando cai o dinheiro de ontem")],
    ),
    SupportPlan: SupportPlan(
        action=SupportAction.CALL_TOOL,
        tool="get_settlement_schedule",
        date_from="2026-08-31",
        date_to="2026-08-31",
    ),
    SupportAnswer: SupportAnswer(answer="Consta R$ 231,26 previsto."),
    HandoffDraft: HandoffDraft(draft="Estamos verificando."),
}


class Model:
    async def structured(self, *, system: str, user: str, schema: type):
        return ACCOUNT[schema]


@pytest.fixture
def service(tmp_path, sample_chunks, fake_collection):
    async def start(exits: AsyncExitStack, model=None, **overrides):
        settings = Settings(state_dir=str(tmp_path / "state"), **overrides)
        storage = await exits.enter_async_context(open_storage(settings))
        retriever = HybridRetriever(sample_chunks, fake_collection(), top_k=5)
        graph = build_graph(
            model or Model(), settings, retriever, storage.checkpointer, storage.queue
        )
        api = create_app()
        serve(api, settings, storage, graph, retriever)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://t")
        return await exits.enter_async_context(client)

    return start


async def ask(http) -> dict:
    payload = {"message": "Quando cai o dinheiro de ontem?", "conversation_id": "c1"}
    response = await http.post("/chat", json=payload, headers=IDENTITY.merchant("cliente1988"))
    return response.json()


async def test_a_turn_is_one_trace_whose_id_is_the_logs_trace_id(service, spans, monkeypatch):
    bound: list[dict] = []
    real_bind = structlog.contextvars.bind_contextvars
    monkeypatch.setattr(
        structlog.contextvars,
        "bind_contextvars",
        lambda **values: bound.append(values) or real_bind(**values),
    )
    async with AsyncExitStack() as exits:
        http = await service(exits)
        await ask(http)

    finished = spans.get_finished_spans()
    names = {span.name for span in finished}
    assert {"POST /chat", "invoke_agent router", "invoke_agent support"} <= names
    assert "execute_tool get_settlement_schedule" in names
    trace_ids = {format(span.context.trace_id, "032x") for span in finished}
    assert trace_ids == {bound[0]["trace_id"]}, "one trace, named in the logs"
    agent = next(span for span in finished if span.name == "invoke_agent support")
    assert agent.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert agent.attributes["gen_ai.agent.name"] == "support"


class Answer(BaseModel):
    answer: str


def openai_stub(sent: list) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        usage = {"prompt_tokens": 1200, "completion_tokens": 10, "total_tokens": 1210}
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-6-luna",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"answer": "ok"}'},
                    }
                ],
                "usage": {**usage, "prompt_tokens_details": {"cached_tokens": 1024}},
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_a_model_call_records_tokens_and_cost_and_no_content(spans):
    sent: list[dict] = []
    provider = OpenAIProvider(
        api_key="sk-test",
        model="gpt-6-luna",
        prices=(0.10, 0.01, 0.50),
        cache=True,
        http_client=openai_stub(sent),
    )
    cost, cached = (
        sample("support_model_cost_usd_total"),
        sample("support_model_tokens_total", {"kind": "cached"}),
    )

    await provider.structured(system="PROMPT ESTÁTICO", user="pergunta do cliente", schema=Answer)

    (span,) = [span for span in spans.get_finished_spans() if span.name == "chat gpt-6-luna"]
    assert span.attributes["gen_ai.provider.name"] == "openai"
    assert span.attributes["gen_ai.usage.input_tokens"] == 1200
    assert span.attributes["gen_ai.usage.output_tokens"] == 10
    assert span.attributes["gen_ai.usage.cache_read.input_tokens"] == 1024
    assert not any(
        "PROMPT" in str(value) or "cliente" in str(value) for value in span.attributes.values()
    )
    # 176 uncached input at 0.10, 1024 cached at 0.01, 10 output at 0.50, per million tokens
    assert sample("support_model_cost_usd_total") - cost == pytest.approx(32.84e-6)
    assert sample("support_model_tokens_total", {"kind": "cached"}) - cached == 1024
    body = sent[0]
    assert body["messages"][0]["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert body["prompt_cache_options"] == {"mode": "explicit", "ttl": "30m"}
    assert len(body["prompt_cache_key"]) == 16


async def test_turn_outcomes_are_counted_by_intent(service):
    labels = {"intent": "account_data", "outcome": "resolved"}
    before = sample("support_turns_total", labels)
    async with AsyncExitStack() as exits:
        http = await service(exits)
        await ask(http)

    assert sample("support_turns_total", labels) - before == 1


@pytest.mark.parametrize(
    ("fault", "kind"), [("model_error", "error"), ("model_timeout", "timeout")]
)
async def test_an_injected_model_fault_escalates_and_counts_as_a_vendor_failure(
    service, fault, kind
):
    """A vendor that hangs still yields the escalation notice: the draft, a best-effort call that
    hangs too, is given up on its own timeout instead of taking the turn down."""
    labels = {"vendor": "model", "kind": kind}
    before = sample("support_vendor_failures_total", labels)
    async with AsyncExitStack() as exits:
        http = await service(exits, FaultyProvider(Model(), {fault}, timeout=0.05))
        reply = await ask(http)

    assert reply["escalation"]["reason"] == "model_unavailable"
    assert sample("support_vendor_failures_total", labels) - before == 1, "once, after retries"


async def test_an_injected_web_outage_raises_and_counts():
    labels = {"vendor": "web", "kind": "error"}
    before = sample("support_vendor_failures_total", labels)
    settings = Settings(tavily_api_key="tvly-test", fault_injection="web_down")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))
    ) as client:
        with pytest.raises(WebSearchUnavailable, match="injected"):
            await WebSearch(settings, client).search("cotação do euro")

    assert sample("support_vendor_failures_total", labels) - before == 1


def test_faults_are_read_from_one_setting():
    settings = Settings(fault_injection=" model_error, web_down ,")

    assert settings.faults() == {"model_error", "web_down"}
