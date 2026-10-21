"""HTTP surface: the conversational endpoint, the operator endpoints and the probes."""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import uuid4

import httpx
import structlog
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.auth import Merchant, RateLimiter, TokenVerifier
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
from app.locks import ConversationLocks
from app.observability import configure_logging, mask_pii
from app.observability.tracing import configure_tracing
from app.operator import router as operator_router
from app.operator import settle
from app.providers.llm import build_provider
from app.rag.retrieve import HybridRetriever
from app.responses import PAUSED_AT, pending_response, to_response
from app.settings import MERCHANT_TIMEZONE, get_settings
from app.storage import open_storage
from app.tools.web import WebSearch


def merchant_today() -> date:
    """The merchant's calendar day: what "ontem" and "amanhã" are relative to, and the day the
    account records are read as of."""
    return datetime.now(MERCHANT_TIMEZONE).date()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the provider, open the store (checkpoints, handoff queue, turn locks) and the web
    client, and compile the graph once, failing fast on bad configuration.

    The index is never built here: a missing one raises with the command that creates it. On
    shutdown uvicorn stops accepting connections and lets in-flight turns finish
    (`--timeout-graceful-shutdown`) before this closes the store under them.
    """
    configure_logging()
    settings = get_settings()
    configure_tracing(settings)
    retriever = HybridRetriever.from_settings(settings)
    provider = build_provider(settings)
    app.state.verifier = TokenVerifier.from_settings(settings)
    app.state.limiter = RateLimiter(settings.rate_limit_per_minute)
    async with open_storage(settings) as storage, httpx.AsyncClient() as http:
        app.state.settings, app.state.retriever = settings, retriever
        app.state.queue, app.state.locks = storage.queue, storage.locks
        app.state.audit = storage.audit
        web = WebSearch(settings, http)
        app.state.graph = build_graph(
            provider, settings, retriever, storage.checkpointer, storage.queue, web
        )
        yield


routes = APIRouter()


@routes.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request, merchant: Merchant) -> ChatResponse:
    """Answer a merchant message inside its conversation, or hand the case to a human.

    The thread is keyed by the merchant the token names together with the conversation id, so a
    conversation id alone never reaches another merchant's history. While a human has this
    conversation's case, the message is noted on the case and gets the open protocol; once the
    case is closed, how it ended is delivered with the next message, before its answer. A second
    message on the same conversation waits for the first one's turn to finish, on any replica.
    """
    trace_id = uuid4().hex
    conversation_id = body.conversation_id or uuid4().hex
    structlog.contextvars.bind_contextvars(trace_id=trace_id, conversation_id=conversation_id)
    thread = thread_id(merchant, conversation_id)
    try:
        async with request.app.state.locks.hold(thread):
            return await take_turn(request.app, body, merchant, conversation_id, thread, trace_id)
    finally:
        structlog.contextvars.clear_contextvars()


async def take_turn(
    app: Any, body: ChatRequest, merchant: str, conversation_id: str, thread: str, trace_id: str
) -> ChatResponse:
    """One turn, under the conversation's lock. A turn left unfinished, because the replica
    running it died, is finished first from its last checkpoint; when this message is the
    merchant's retry of it, that is the answer."""
    graph, config = app.state.graph, {"configurable": {"thread_id": thread}}
    snapshot = await graph.aget_state(config)
    if snapshot.next and PAUSED_AT not in snapshot.next:
        finished = await graph.ainvoke(None, config, durability="sync")
        if finished.get("message") == body.message:
            return to_response(finished, conversation_id)
        snapshot = await graph.aget_state(config)
    if PAUSED_AT in snapshot.next and await still_waiting(app, snapshot.values, body.message):
        return pending_response(snapshot.values, conversation_id)
    delivered = await app.state.queue.deliver(thread)
    turn = turn_input(
        message=body.message,
        user_id=merchant,
        conversation_id=conversation_id,
        trace_id=trace_id,
        today=merchant_today(),
    )
    final = await graph.ainvoke(turn, config, durability="sync")
    return to_response(final, conversation_id).model_copy(update={"delivered": delivered})


async def still_waiting(app: Any, values: dict, message: str) -> bool:
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
    if expired is not None:
        await app.state.audit.append("system", "system", "case.expired", record.case.protocol)
    return expired is None


ConversationId = Annotated[str, PathParam(pattern=CONVERSATION_ID_PATTERN)]


@routes.get("/conversations/{conversation_id}", response_model=ConversationView)
async def conversation(
    conversation_id: ConversationId, request: Request, merchant: Merchant
) -> ConversationView:
    """A conversation's history, as stored, and the protocol of its open case if one is waiting.

    Read through the same thread key as `/chat`, so another merchant's conversation is a 404."""
    config = {"configurable": {"thread_id": thread_id(merchant, conversation_id)}}
    snapshot = await request.app.state.graph.aget_state(config)
    if not snapshot.values:
        raise HTTPException(404, "no such conversation")
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


@routes.delete("/me/conversations/{conversation_id}", status_code=204)
async def erase(conversation_id: ConversationId, request: Request, merchant: Merchant) -> Response:
    """LGPD erasure: the conversation's checkpoints and every case it opened, payloads, notes and
    replies included, under its lock so no turn writes it back. The audit log keeps that it
    happened, by thread hash, and nothing of what was said."""
    app, thread = request.app, thread_id(merchant, conversation_id)
    async with app.state.locks.hold(thread):
        graph = app.state.graph
        if not (await graph.aget_state({"configurable": {"thread_id": thread}})).values:
            raise HTTPException(404, "no such conversation")
        await graph.checkpointer.adelete_thread(thread)
        await app.state.queue.erase(thread)
        await app.state.audit.append(merchant, "merchant", "conversation.erased", thread)
    return Response(status_code=204)


@routes.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness, for a load balancer: the store answers and the index is loaded. 503 names what
    is not ready; `/health` only says the process is up."""
    problems = []
    try:
        await request.app.state.queue.ping()
    except Exception as exc:  # any store failure means this replica should not take traffic
        problems.append(f"storage: {type(exc).__name__}")
    chunks = request.app.state.retriever.size
    if not chunks:
        problems.append("index: empty")
    if problems:
        return JSONResponse({"status": "not_ready", "problems": problems}, status_code=503)
    storage = request.app.state.settings.storage
    return JSONResponse({"status": "ready", "storage": storage, "chunks": chunks})


@routes.get("/metrics")
async def metrics() -> Response:
    """Escalations by reason and node latency, in the Prometheus text format."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@routes.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


def create_app() -> FastAPI:
    """The API: one per process, or two in a test that plays two replicas."""
    api = FastAPI(title="Multi-agent support system", lifespan=lifespan)
    api.include_router(routes)
    api.include_router(operator_router)
    api.state.locks, api.state.limiter = ConversationLocks(), RateLimiter()
    return api


app = create_app()
