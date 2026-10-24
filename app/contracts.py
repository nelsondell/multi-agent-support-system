"""API and cross-node contracts.

`user_id` appears in the request envelope only. It is never a field of a model the LLM can read
or write, which is what keeps authorization out of prompt space.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Intent(StrEnum):
    """What the merchant is asking for, independent of which agent serves it."""

    PRODUCT_INFO = "product_info"
    TROUBLESHOOTING = "troubleshooting"
    ACCOUNT_DATA = "account_data"
    INDIVIDUAL_CASE = "individual_case"
    TOOL_UNAVAILABLE = "tool_unavailable"
    GENERAL_INFO = "general_info"
    """A general-purpose question with nothing to do with the acquirer: weather, exchange rates."""
    OTHER = "other"


class TargetAgent(StrEnum):
    """The specialist a decision points to."""

    KNOWLEDGE = "knowledge"
    SUPPORT = "support"


class Need(BaseModel):
    """A question a specialist found only the other one can answer."""

    agent: TargetAgent
    query_pt: str = Field(min_length=3, max_length=300)
    """The question, as a standalone Portuguese sentence."""


class GuardrailOutcome(StrEnum):
    """What the guardrail decided before routing."""

    ALLOW = "allow"
    PROMPT_INJECTION = "prompt_injection"
    IDENTITY_SWAP = "identity_swap"


class EscalationReason(StrEnum):
    """Why a case left the automated path. Doubles as the operational metric."""

    LOW_ROUTER_CONFIDENCE = "low_router_confidence"
    REPEATED_MISROUTE = "repeated_misroute"
    LOW_RETRIEVAL_CONFIDENCE = "low_retrieval_confidence"
    INSUFFICIENT_SOURCES = "insufficient_sources"
    INDIVIDUAL_CASE = "individual_case"
    TOOL_UNAVAILABLE = "tool_unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    BUDGET_EXHAUSTED = "budget_exhausted"

    @classmethod
    def _missing_(cls, value: object) -> "EscalationReason | None":
        """Cases and checkpoints stored before the rename still load."""
        return cls.MODEL_UNAVAILABLE if value == "agent_not_implemented" else None


class SourceType(StrEnum):
    """Where the evidence behind an answer came from."""

    KB = "kb"
    WEB = "web"
    ACCOUNT = "account"
    OPERATOR = "operator"


_PAGES = (SourceType.KB, SourceType.WEB)


class Source(BaseModel):
    """The evidence behind an answer: a corpus or web page, the merchant's account, or a human."""

    url: str | None = None
    title: str
    """The page title; for the account, the tool that read it; for an operator, the protocol."""
    source_type: SourceType = SourceType.KB

    @model_validator(mode="after")
    def check_url(self) -> "Source":
        """Pages are cited by url; the account and an operator are not pages."""
        if (self.url is not None) != (self.source_type in _PAGES):
            raise ValueError("kb and web sources carry a url, account and operator sources do not")
        return self


class Escalation(BaseModel):
    """Structured handoff payload, built without an LLM call."""

    protocol: str
    reason: EscalationReason
    summary: str


class HandoffCase(BaseModel):
    """What the operator receives when a turn pauses for a human."""

    protocol: str
    reason: EscalationReason
    intent: Intent | None = None
    summary: str
    """The merchant's message, masked."""
    attempts: list[str] = Field(default_factory=list)
    """What the agents tried and why it stopped, one line each."""
    sources: list[Source] = Field(default_factory=list)
    """Pages retrieved along the way, even if none was close enough to answer from."""
    proposed_draft: str | None = None
    """A reply the operator can approve as is. Empty when it could not be drafted."""
    user_id: str
    conversation_id: str
    opened_at: datetime
    expires_at: datetime | None = None
    """After this, the merchant's next message closes the case as expired and the conversation
    goes on. Empty only on cases recorded before expiry existed."""


Outcome = Literal["answered", "cancelled", "expired"]


class CaseNote(BaseModel):
    """Something the merchant wrote while the case was waiting, masked."""

    at: datetime
    message: str


class HandoffRecord(BaseModel):
    """A case as the operator endpoints show it."""

    status: Literal["open", "closed"]
    outcome: Outcome | None = None
    """How a closed case ended."""
    case: HandoffCase
    closed_at: datetime | None = None
    notes: list[CaseNote] = Field(default_factory=list)


class OperatorInput(BaseModel):
    """The operator's decision: send the proposed draft, answer in their own words, or close the
    case without an answer."""

    action: Literal["approve", "reply", "cancel"]
    answer: str | None = None

    @model_validator(mode="after")
    def check_answer(self) -> "OperatorInput":
        """A reply needs words; an approval and a cancellation take none."""
        if self.action == "reply" and not (self.answer and self.answer.strip()):
            raise ValueError("a reply needs a non-empty answer")
        if self.action != "reply" and self.answer is not None:
            raise ValueError(f"{self.action} takes no answer")
        return self


class DeliveredReply(BaseModel):
    """How a case ended, as the merchant receives it on their next message."""

    protocol: str
    answer: str
    sources: list[Source] = Field(default_factory=list)


CONVERSATION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class ChatRequest(BaseModel):
    """Inbound payload. The merchant is whoever the bearer token names."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    conversation_id: str | None = Field(default=None, pattern=CONVERSATION_ID_PATTERN)
    """Continues a conversation when given; the server starts a new one when absent. It only
    names a thread together with the merchant's id, never on its own."""
    confirm_action_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    """Runs the proposed write when it is that proposal's id; any other message cancels it."""
    user_id: str | None = None
    """Accepted so the challenge's `{"message", "user_id"}` payload works with a token: it must be
    the token's own merchant, or the request is a 403. It never selects whose data is read."""


class PendingAction(BaseModel):
    """A write only the merchant can run, rendered by code, never summarised by a model."""

    id: str
    operation: str


class ChatResponse(BaseModel):
    """Outbound payload. `answer` is always Brazilian Portuguese."""

    conversation_id: str
    status: Literal["answered", "pending_human", "pending_confirmation"] = "answered"
    """`pending_human` while an operator has the case; the answer is then a handoff notice.
    `pending_confirmation` while a proposed write waits for its `confirm_action_id`."""
    answer: str
    intent: Intent
    agents: list[TargetAgent] = Field(default_factory=list)
    """The specialists that ran, in plan order. Empty when routing never reached one."""
    confidence: float = 0.0
    sources: list[Source] = Field(default_factory=list)
    escalated: bool = False
    escalation: Escalation | None = None
    delivered: list[DeliveredReply] = Field(default_factory=list)
    """How this conversation's cases ended since the merchant last wrote: an operator's reply, or
    a notice that a case was cancelled or expired. Delivered before this turn's answer."""
    pending_action: PendingAction | None = None

    @model_validator(mode="after")
    def check_consistency(self) -> "ChatResponse":
        """Reject responses that say two different things about what happened."""
        if self.escalated != (self.escalation is not None):
            raise ValueError("escalated must match the presence of an escalation payload")
        if self.status == "pending_human" and not self.escalated:
            raise ValueError("only an escalated turn can be waiting for a human")
        operator = any(s.source_type is SourceType.OPERATOR for s in self.sources)
        if operator and (not self.escalated or self.status == "pending_human"):
            raise ValueError("an operator source belongs to an escalated turn an operator answered")
        if not self.answer.strip():
            raise ValueError("a response must carry an answer")
        if (
            self.escalation is not None
            and self.escalation.reason is EscalationReason.LOW_ROUTER_CONFIDENCE
            and self.agents
        ):
            raise ValueError("routing never reached a specialist, so none may be reported")
        return self


class HistoryMessage(BaseModel):
    """One message of a conversation, as stored: the merchant's masked."""

    role: Literal["merchant", "assistant"]
    content: str


class ConversationView(BaseModel):
    """A conversation's history, and whether a human has its case right now."""

    conversation_id: str
    status: Literal["active", "pending_human"]
    protocol: str | None = None
    """The open case's protocol while one is waiting."""
    summary: str = ""
    """Rolling summary of the messages that left the history window."""
    messages: list[HistoryMessage] = Field(default_factory=list)
