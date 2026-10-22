"""Spans in the OTel GenAI conventions (in Development): names, models and counts, no content."""

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
    provider = TracerProvider(resource=Resource.create({"service.name": "support-api"}))
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


def trace_id(span: trace.Span) -> str:
    """32 hex digits, or a fresh id when no tracer is installed."""
    context = span.get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else uuid4().hex


@contextmanager
def operation(kind: str, name: str, **attributes: Any) -> Iterator[trace.Span]:
    """A GenAI span; a graph interrupt is control flow, not an error."""
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
