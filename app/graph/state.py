"""The contract between graph nodes.

The state carries only what has to cross a node boundary. Retrieved chunks stay inside the
knowledge agent; only the sources that actually back an answer are promoted here.
"""

from datetime import date
from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.contracts import (
    Escalation,
    GuardrailOutcome,
    HandoffCase,
    Need,
    Source,
    SourceType,
    TargetAgent,
)
from app.graph.actions import ProposedAction
from app.graph.router import PlanStep, RouterDecision


class StepResult(BaseModel):
    """What one plan step produced: an answer with its sources, or a reason it stopped."""

    agent: TargetAgent
    query_pt: str
    requested_by: TargetAgent | None = None
    answer: str | None = None
    sources: list[Source] = Field(default_factory=list)
    refusal_reason: str | None = None
    delivered: bool = False
    """Already sent, as the answered part of a turn that then paused for a human."""

    @property
    def by_operator(self) -> bool:
        """Answered by a human after the step paused, rather than by its specialist."""
        return any(source.source_type is SourceType.OPERATOR for source in self.sources)


class GraphState(BaseModel):
    """The state of one conversation thread.

    `messages` and `summary` belong to the conversation and persist across turns in the
    checkpoint. Every other field belongs to the current turn and is reset when the next one
    starts, by `memory.turn_input`.
    """

    message: str
    user_id: str
    """Comes from the authenticated request and is never exposed to a model schema."""

    conversation_id: str = ""
    """The caller's conversation; with `user_id` it names this thread."""

    trace_id: str = ""
    """Correlates every log line of one request."""

    today: date | None = None
    """The merchant's calendar date when the turn arrived, which gives "yesterday" a date."""

    masked_message: str = ""
    """Filled by the guardrail; this is the only form that reaches a log or the history."""

    guardrail_outcome: GuardrailOutcome | None = None
    route: RouterDecision | None = None

    plan: list[PlanStep] = Field(default_factory=list)
    """The router's steps plus any the supervisor added."""

    hops: int = 0
    """Router passes so far. Bounds the single hand-back budget across the whole plan."""

    plan_changes: int = 0
    """Steps the supervisor added this turn, against `MAX_PLAN_CHANGES`."""

    step_index: int = 0
    """The plan step running now."""

    need: Need | None = None
    """What the running step found it cannot answer alone."""

    step_results: list[StepResult] = Field(default_factory=list)
    """One entry per finished step, in plan order. Steps run one at a time, so no reducer."""

    handoff_reason: str | None = None
    """Set by a specialist that declines the request, read by the router on the second pass."""

    refusal_reason: str | None = None
    """Set by a specialist that could answer but must not: terminal, unlike a hand-off."""

    answer: str | None = None
    sources: list[Source] = Field(default_factory=list)
    """The evidence the answer was built from. Empty when nothing was answered."""

    partial_answer: str | None = None
    """When one step answered and another needs a human: the part delivered right away."""

    consulted: list[Source] = Field(default_factory=list)
    """Pages a specialist retrieved, whether or not they were good enough to answer from."""

    evidence: list[str] = Field(default_factory=list)
    """The text behind this turn: retrieved chunks and tool results. Checked against anything the
    system rephrases; never sent to the merchant and never stored in the history."""

    escalation: Escalation | None = None
    case: HandoffCase | None = None
    """What the operator sees while this turn waits for a human."""
    pending_action: ProposedAction | None = None

    messages: Annotated[list[AnyMessage], add_messages] = Field(default_factory=list)
    """Completed exchanges, oldest first: the masked merchant turn and the reply."""

    summary: str = ""
    """Rolling summary of the exchanges that left the history window."""

    @property
    def steps(self) -> list[PlanStep]:
        """`plan`, or the router's steps for a turn checkpointed before `plan` existed."""
        if self.plan or self.route is None:
            return self.plan
        return list(self.route.steps)

    def current_step(self) -> PlanStep | None:
        """The step a specialist is answering, or None before a plan exists."""
        steps = self.steps
        return steps[self.step_index] if self.step_index < len(steps) else None

    def current_query(self) -> str:
        """The standalone question of the running step, or the raw message without a plan."""
        step = self.current_step()
        return step.query_pt if step and step.query_pt else self.message
