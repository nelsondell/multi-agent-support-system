"""HTTP surface: one conversational endpoint and a liveness probe."""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from app.contracts import ChatRequest, ChatResponse, Intent
from app.graph.build import build_graph
from app.graph.state import GraphState
from app.providers.llm import build_provider
from app.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the provider and compile the graph once, failing fast on bad configuration."""
    settings = get_settings()
    app.state.graph = build_graph(build_provider(settings), settings)
    yield


app = FastAPI(title="Multi-agent support system", lifespan=lifespan)


def to_response(final: dict[str, Any]) -> ChatResponse:
    """Project the terminal graph state onto the public contract."""
    state = GraphState.model_validate(final)
    decision = state.route
    return ChatResponse(
        answer=state.answer or "",
        intent=decision.intent if decision else Intent.OTHER,
        target_agent=decision.target_agent if decision else None,
        confidence=decision.confidence if decision else 0.0,
        sources=state.sources,
        escalated=state.escalation is not None,
        escalation=state.escalation,
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Answer a merchant message, or hand the case to a human."""
    initial = GraphState(message=request.message, user_id=request.user_id)
    final = await app.state.graph.ainvoke(initial)
    return to_response(final)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}
