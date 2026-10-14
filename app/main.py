"""HTTP surface: one conversational endpoint and a liveness probe."""

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import structlog
from fastapi import FastAPI

from app.checkpoints import open_checkpointer
from app.contracts import ChatRequest, ChatResponse, EscalationReason, Intent
from app.graph.build import build_graph
from app.graph.memory import thread_id, turn_input
from app.graph.state import GraphState
from app.observability import configure_logging
from app.providers.llm import build_provider
from app.rag.retrieve import HybridRetriever
from app.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the provider, open the conversation store and compile the graph once, failing fast
    on bad configuration.

    The index is never built here: a missing one raises with the command that creates it.
    """
    configure_logging()
    settings = get_settings()
    retriever = HybridRetriever.from_settings(settings)
    provider = build_provider(settings)
    async with open_checkpointer(settings) as checkpointer:
        app.state.graph = build_graph(provider, settings, retriever, checkpointer)
        yield


app = FastAPI(title="Multi-agent support system", lifespan=lifespan)


def to_response(final: dict[str, Any], conversation_id: str) -> ChatResponse:
    """Project the terminal graph state onto the public contract."""
    state = GraphState.model_validate(final)
    decision = state.route
    reached_specialist = state.escalation is None or (
        state.escalation.reason is not EscalationReason.LOW_ROUTER_CONFIDENCE
    )
    return ChatResponse(
        conversation_id=conversation_id,
        answer=state.answer or "",
        intent=decision.intent if decision else Intent.OTHER,
        target_agent=decision.target_agent if decision and reached_specialist else None,
        confidence=decision.confidence if decision else 0.0,
        sources=state.sources,
        escalated=state.escalation is not None,
        escalation=state.escalation,
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Answer a merchant message inside its conversation, or hand the case to a human.

    The thread is keyed by the authenticated `user_id` together with the conversation id, so a
    conversation id alone never reaches another merchant's history.
    """
    trace_id = uuid4().hex
    conversation_id = request.conversation_id or uuid4().hex
    structlog.contextvars.bind_contextvars(trace_id=trace_id, conversation_id=conversation_id)
    config = {"configurable": {"thread_id": thread_id(request.user_id, conversation_id)}}
    try:
        turn = turn_input(request.message, request.user_id, trace_id)
        return to_response(await app.state.graph.ainvoke(turn, config), conversation_id)
    finally:
        structlog.contextvars.clear_contextvars()


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}
