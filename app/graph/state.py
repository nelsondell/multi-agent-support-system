"""The contract between graph nodes.

The state carries only what has to cross a node boundary. Retrieved chunks stay inside the
knowledge agent; only the sources that actually back an answer are promoted here.
"""

from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.contracts import Escalation, GuardrailOutcome, Source
from app.graph.router import RouterDecision


class GraphState(BaseModel):
    """The state of one conversation thread.

    `messages` and `summary` belong to the conversation and persist across turns in the
    checkpoint. Every other field belongs to the current turn and is reset when the next one
    starts, by `memory.turn_input`.
    """

    message: str
    user_id: str
    """Comes from the authenticated request and is never exposed to a model schema."""

    trace_id: str = ""
    """Correlates every log line of one request."""

    masked_message: str = ""
    """Filled by the guardrail; this is the only form that reaches a log or the history."""

    guardrail_outcome: GuardrailOutcome | None = None
    route: RouterDecision | None = None

    hops: int = 0
    """Router passes so far. Bounds the single hand-back budget."""

    handoff_reason: str | None = None
    """Set by a specialist that declines the request, read by the router on the second pass."""

    refusal_reason: str | None = None
    """Set by a specialist that could answer but must not: terminal, unlike a hand-off."""

    answer: str | None = None
    sources: list[Source] = Field(default_factory=list)
    escalation: Escalation | None = None

    messages: Annotated[list[AnyMessage], add_messages] = Field(default_factory=list)
    """Completed exchanges, oldest first: the masked merchant turn and the reply."""

    summary: str = ""
    """Rolling summary of the exchanges that left the history window."""
