"""The contract between graph nodes.

The state carries only what has to cross a node boundary. Retrieved chunks stay inside the
knowledge agent; only the sources that actually back an answer are promoted here.
"""

from pydantic import BaseModel, Field

from app.contracts import Escalation, GuardrailOutcome, Source
from app.graph.router import RouterDecision


class GraphState(BaseModel):
    """Per-request state. One request, one answer: there is no conversation history."""

    message: str
    user_id: str
    """Comes from the authenticated request and is never exposed to a model schema."""

    masked_message: str = ""
    """Filled by the guardrail; this is the only form that reaches a log."""

    guardrail_outcome: GuardrailOutcome | None = None
    route: RouterDecision | None = None

    hops: int = 0
    """Router passes so far. Bounds the single hand-back budget."""

    handoff_reason: str | None = None
    """Set by a specialist that declines the request, read by the router on the second pass."""

    answer: str | None = None
    sources: list[Source] = Field(default_factory=list)
    escalation: Escalation | None = None
