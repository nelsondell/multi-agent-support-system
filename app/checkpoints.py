"""The conversation store: one SQLite file of LangGraph checkpoints under `STATE_DIR`.

Every turn loads its thread from here, and by default LangGraph's decoder imports whatever type a
checkpoint names. The serializer below allows only the built-ins LangGraph marks as safe plus the
application types the graph state actually holds, so a tampered checkpoint is read back as plain
data instead of making the process import arbitrary code.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.contracts import (
    Escalation,
    EscalationReason,
    GuardrailOutcome,
    HandoffCase,
    Intent,
    Source,
    SourceType,
    TargetAgent,
)
from app.graph.router import PlanStep, RouterDecision
from app.graph.state import StepResult
from app.settings import Settings

STATE_TYPES: tuple[type, ...] = (
    GuardrailOutcome,
    Intent,
    TargetAgent,
    EscalationReason,
    Source,
    SourceType,
    Escalation,
    HandoffCase,
    PlanStep,
    RouterDecision,
    StepResult,
)
"""Every application type the graph state can hold. A type missing here comes back from a
checkpoint as a plain dict or string; a test checks the list against `GraphState`."""


def checkpoint_serializer() -> JsonPlusSerializer:
    """A serializer that decodes nothing outside LangGraph's safe set and `STATE_TYPES`."""
    allowed = [(kind.__module__, kind.__qualname__) for kind in STATE_TYPES]
    return JsonPlusSerializer(allowed_msgpack_modules=allowed)


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[AsyncSqliteSaver]:
    """Open the checkpoint database, creating its directory on first use."""
    state_dir = Path(settings.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(state_dir / "checkpoints.sqlite")) as connection:
        yield AsyncSqliteSaver(connection, serde=checkpoint_serializer())
