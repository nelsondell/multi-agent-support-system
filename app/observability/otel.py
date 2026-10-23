"""OpenTelemetry traces in the GenAI semantic conventions.

Each request is one trace: a root span for the HTTP call, an `invoke_agent` span per graph node,
a `chat` span per model call (provider, model, input, cached and output tokens) and an
`execute_tool` span per tool call. No prompt, message or answer is ever recorded: names, models
and counts only. The GenAI conventions are still in Development status, so attribute names may
change with them. Spans go to `OTEL_EXPORTER_OTLP_ENDPOINT` when it is set; the root span's trace
id is also the `trace_id` of every log line of the request.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.settings import Settings

TRACER = trace.get_tracer("support")


def configure_otel(settings: Settings) -> None:
    """Install the tracer provider, exporting over OTLP only when an endpoint is configured."""
    provider = TracerProvider(resource=Resource.create({"service.name": "support-api"}))
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


def trace_id(span: trace.Span) -> str:
    """The span's trace id as 32 hex digits, or a fresh id when no tracer is installed."""
    context = span.get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else uuid4().hex


@contextmanager
def operation(kind: str, name: str, **attributes: Any) -> Iterator[trace.Span]:
    """A GenAI span: `invoke_agent <agent>`, `chat <model>` or `execute_tool <tool>`. A graph
    interrupt passes through as control flow, not as an error."""
    from langgraph.errors import GraphBubbleUp

    span_attributes = {"gen_ai.operation.name": kind, **attributes}
    with TRACER.start_as_current_span(
        f"{kind} {name}",
        attributes=span_attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except GraphBubbleUp:
            raise
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, type(exc).__name__))
            raise
