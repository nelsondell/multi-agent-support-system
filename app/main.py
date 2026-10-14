"""HTTP surface: the conversational endpoint, the operator endpoints and a liveness probe."""

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import httpx
import structlog
from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.checkpoints import open_checkpointer
from app.contracts import ChatRequest, ChatResponse
from app.graph.build import build_graph
from app.graph.memory import thread_id, turn_input
from app.handoffs import open_handoff_queue
from app.observability import configure_logging
from app.observability.tracing import configure_tracing
from app.operator import router as operator_router
from app.providers.llm import build_provider
from app.rag.retrieve import HybridRetriever
from app.responses import PAUSED_AT, pending_response, to_response
from app.settings import get_settings
from app.tools.web import WebSearch

MERCHANT_TIMEZONE = timezone(timedelta(hours=-3), "America/Sao_Paulo")
"""Brazil has had no daylight saving time since 2019, so a fixed offset is exact and needs no
timezone database in the image."""


def merchant_today() -> date:
    """The merchant's calendar day: what "ontem" and "amanhã" are relative to, and the day the
    account records are read as of."""
    return datetime.now(MERCHANT_TIMEZONE).date()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the provider, open the conversation store, the handoff queue and the web client,
    and compile the graph once, failing fast on bad configuration.

    The index is never built here: a missing one raises with the command that creates it.
    """
    configure_logging()
    settings = get_settings()
    configure_tracing(settings)
    retriever = HybridRetriever.from_settings(settings)
    provider = build_provider(settings)
    async with (
        open_checkpointer(settings) as checkpointer,
        open_handoff_queue(settings) as queue,
        httpx.AsyncClient() as http,
    ):
        app.state.settings = settings
        app.state.queue = queue
        web = WebSearch(settings, http)
        app.state.graph = build_graph(provider, settings, retriever, checkpointer, queue, web)
        yield


app = FastAPI(title="Multi-agent support system", lifespan=lifespan)
app.include_router(operator_router)


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Answer a merchant message inside its conversation, or hand the case to a human.

    The thread is keyed by the authenticated `user_id` together with the conversation id, so a
    conversation id alone never reaches another merchant's history. While a human has this
    conversation's case, the message gets the open protocol and no new run starts.
    """
    trace_id = uuid4().hex
    conversation_id = request.conversation_id or uuid4().hex
    structlog.contextvars.bind_contextvars(trace_id=trace_id, conversation_id=conversation_id)
    config = {"configurable": {"thread_id": thread_id(request.user_id, conversation_id)}}
    try:
        snapshot = await app.state.graph.aget_state(config)
        if PAUSED_AT in snapshot.next:
            return pending_response(snapshot.values, conversation_id)
        turn = turn_input(
            message=request.message,
            user_id=request.user_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            today=merchant_today(),
        )
        return to_response(await app.state.graph.ainvoke(turn, config), conversation_id)
    finally:
        structlog.contextvars.clear_contextvars()


@app.get("/metrics")
async def metrics() -> Response:
    """Escalations by reason and node latency, in the Prometheus text format."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}
