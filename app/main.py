"""HTTP surface: the conversational endpoint, the operator endpoints and a liveness probe."""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Annotated
from uuid import uuid4

import httpx
import structlog
from fastapi import FastAPI, Query, Response
from fastapi import Path as PathParam
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.checkpoints import open_checkpointer
from app.contracts import (
    CONVERSATION_ID_PATTERN,
    ChatRequest,
    ChatResponse,
    ConversationView,
    HistoryMessage,
    OperatorInput,
)
from app.graph.build import build_graph
from app.graph.memory import thread_id, turn_input
from app.graph.state import GraphState
from app.handoffs import open_handoff_queue
from app.observability import configure_logging, mask_pii
from app.observability.tracing import configure_tracing
from app.operator import router as operator_router
from app.operator import settle
from app.providers.llm import build_provider
from app.rag.retrieve import HybridRetriever
from app.responses import PAUSED_AT, pending_response, to_response
from app.settings import MERCHANT_TIMEZONE, get_settings
from app.tools.web import WebSearch


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
    conversation's case, the message is noted on the case and gets the open protocol; once the
    case is closed, how it ended is delivered with the next message, before its answer.
    """
    trace_id = uuid4().hex
    conversation_id = request.conversation_id or uuid4().hex
    structlog.contextvars.bind_contextvars(trace_id=trace_id, conversation_id=conversation_id)
    thread = thread_id(request.user_id, conversation_id)
    config = {"configurable": {"thread_id": thread}}
    try:
        snapshot = await app.state.graph.aget_state(config)
        if PAUSED_AT in snapshot.next and await still_waiting(snapshot.values, request.message):
            return pending_response(snapshot.values, conversation_id)
        delivered = await app.state.queue.deliver(thread)
        turn = turn_input(
            message=request.message,
            user_id=request.user_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            today=merchant_today(),
        )
        response = to_response(await app.state.graph.ainvoke(turn, config), conversation_id)
        return response.model_copy(update={"delivered": delivered})
    finally:
        structlog.contextvars.clear_contextvars()


async def still_waiting(values: dict, message: str) -> bool:
    """Whether a paused conversation keeps waiting for its operator. While the case is open and
    not expired, the message becomes a note on it. An expired case is closed here, with its
    notice held for delivery, and the conversation goes on."""
    queue = app.state.queue
    found = await queue.get(GraphState.model_validate(values).escalation.protocol)
    if found is None or found[0].status == "closed":
        return True
    record, thread = found
    expires_at = record.case.expires_at
    if expires_at is None or expires_at > datetime.now(UTC):
        await queue.add_note(record.case.protocol, mask_pii(message))
        return True
    expired = await settle(app, record, thread, OperatorInput(action="cancel"), "expired")
    return expired is None


@app.get("/conversations/{conversation_id}", response_model=ConversationView)
async def conversation(
    user_id: Annotated[str, Query(min_length=1)],
    conversation_id: Annotated[str, PathParam(pattern=CONVERSATION_ID_PATTERN)],
) -> ConversationView:
    """A conversation's history, as stored, and the protocol of its open case if one is waiting.

    Read through the same thread key as `/chat`, so another merchant's id finds nothing."""
    config = {"configurable": {"thread_id": thread_id(user_id, conversation_id)}}
    snapshot = await app.state.graph.aget_state(config)
    if not snapshot.values:
        return ConversationView(conversation_id=conversation_id, status="active")
    state = GraphState.model_validate(snapshot.values)
    paused = PAUSED_AT in snapshot.next
    return ConversationView(
        conversation_id=conversation_id,
        status="pending_human" if paused else "active",
        protocol=state.escalation.protocol if paused and state.escalation else None,
        summary=state.summary,
        messages=[
            HistoryMessage(
                role="merchant" if message.type == "human" else "assistant",
                content=str(message.content),
            )
            for message in state.messages
        ],
    )


@app.get("/metrics")
async def metrics() -> Response:
    """Escalations by reason and node latency, in the Prometheus text format."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}
