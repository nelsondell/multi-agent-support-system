"""A turn is one trace whose id is in its logs, and is counted by outcome; a model call records
tokens, cache reads and cost, never content, and sends its static prompt as a cache breakpoint."""

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
from app.graph.router import PlanStep, RouterDecision
from app.graph.support import SupportAction, SupportAnswer, SupportPlan
from app.providers.llm import LLMTimeout, OpenAIProvider
from tests.identity import IDENTITY

SPANS = InMemorySpanExporter()
STEP = PlanStep(agent=TargetAgent.SUPPORT, query_pt="qual é a minha maquininha")
ANSWERS = {
    RouterDecision: RouterDecision(
        intent=Intent.ACCOUNT_DATA, confidence=0.9, rationale="t", steps=[STEP]
    ),
    SupportPlan: SupportPlan(action=SupportAction.CALL_TOOL, tool="get_account_profile"),
    SupportAnswer: SupportAnswer(answer="Sua maquininha é a Get Smart."),
}


@pytest.fixture(scope="module", autouse=True)
def tracer_provider():  # once per process, as OpenTelemetry requires
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(SPANS))
    trace.set_tracer_provider(provider)


def sample(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class Model:
    async def structured(self, *, system: str, user: str, schema: type):
        return ANSWERS[schema]


async def test_a_turn_is_one_trace_named_in_its_logs_and_counted(api, monkeypatch):
    SPANS.clear()
    bound: list[dict] = []
    real_bind = structlog.contextvars.bind_contextvars
    monkeypatch.setattr(
        structlog.contextvars, "bind_contextvars", lambda **v: bound.append(v) or real_bind(**v)
    )
    resolved = {"intent": "account_data", "outcome": "resolved"}
    before = sample("support_turns_total", **resolved)
    async with AsyncExitStack() as exits:
        http = await api(exits, Model())
        payload = {"message": "Qual é a minha maquininha?", "conversation_id": "c1"}
        await http.post("/chat", json=payload, headers=IDENTITY.merchant("cliente1988"))

    finished = SPANS.get_finished_spans()
    names = {span.name for span in finished}
    assert {"POST /chat", "invoke_agent router", "execute_tool get_account_profile"} <= names
    assert {format(span.context.trace_id, "032x") for span in finished} == {bound[0]["trace_id"]}
    agent = next(span for span in finished if span.name == "invoke_agent support")
    assert agent.attributes["gen_ai.agent.name"] == "support"
    assert sample("support_turns_total", **resolved) - before == 1


class Answer(BaseModel):
    answer: str


def openai_stub(sent: list) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        usage = {"prompt_tokens": 1200, "completion_tokens": 10, "total_tokens": 1210}
        message = {"role": "assistant", "content": '{"answer": "ok"}'}
        body = {"id": "x", "object": "chat.completion", "created": 0, "model": "gpt-6-luna"}
        body |= {"choices": [{"index": 0, "finish_reason": "stop", "message": message}]}
        body["usage"] = {**usage, "prompt_tokens_details": {"cached_tokens": 1024}}
        return httpx.Response(200, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_a_model_call_records_tokens_and_cost_and_no_content():
    SPANS.clear()
    sent: list[dict] = []
    provider = OpenAIProvider(
        api_key="sk-test",
        model="gpt-6-luna",
        prices=(0.10, 0.01, 0.50),
        cache=True,
        http_client=openai_stub(sent),
    )
    cost = sample("support_model_cost_usd_total")

    await provider.structured(system="PROMPT ESTÁTICO", user="pergunta do cliente", schema=Answer)

    (span,) = SPANS.get_finished_spans()
    assert span.name == "chat gpt-6-luna" and span.attributes["gen_ai.provider.name"] == "openai"
    usage = ("input_tokens", "output_tokens", "cache_read.input_tokens")
    assert [span.attributes[f"gen_ai.usage.{name}"] for name in usage] == [1200, 10, 1024]
    assert not any("PROMPT" in str(v) or "cliente" in str(v) for v in span.attributes.values())
    # 176 uncached input at 0.10, 1024 cached at 0.01, 10 output at 0.50, per million tokens
    assert sample("support_model_cost_usd_total") - cost == pytest.approx(32.84e-6)
    assert sent[0]["messages"][0]["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
    assert sent[0]["prompt_cache_options"] == {"mode": "explicit", "ttl": "30m"}
    assert len(sent[0]["prompt_cache_key"]) == 16


async def test_a_vendor_that_does_not_answer_in_time_is_an_llm_timeout():
    def hang(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no answer", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(hang))
    provider = OpenAIProvider(api_key="sk-test", model="m", max_retries=0, http_client=client)

    with pytest.raises(LLMTimeout):
        await provider.structured(system="s", user="u", schema=Answer)
