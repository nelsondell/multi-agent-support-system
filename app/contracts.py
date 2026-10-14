"""API and cross-node contracts.

`user_id` appears in the request envelope only. It is never a field of a model the LLM can read
or write, which is what keeps authorization out of prompt space.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Intent(StrEnum):
    """What the merchant is asking for, independent of which agent serves it."""

    PRODUCT_INFO = "product_info"
    TROUBLESHOOTING = "troubleshooting"
    ACCOUNT_DATA = "account_data"
    INDIVIDUAL_CASE = "individual_case"
    GENERAL_INFO = "general_info"
    """A general-purpose question with nothing to do with the acquirer: weather, exchange rates."""
    OTHER = "other"


class TargetAgent(StrEnum):
    """The specialist a decision points to."""

    KNOWLEDGE = "knowledge"
    SUPPORT = "support"


class GuardrailOutcome(StrEnum):
    """What the guardrail decided before routing."""

    ALLOW = "allow"
    IDENTITY_SWAP = "identity_swap"


class EscalationReason(StrEnum):
    """Why a case left the automated path. Doubles as the operational metric."""

    LOW_ROUTER_CONFIDENCE = "low_router_confidence"
    REPEATED_MISROUTE = "repeated_misroute"
    LOW_RETRIEVAL_CONFIDENCE = "low_retrieval_confidence"
    INDIVIDUAL_CASE = "individual_case"
    AGENT_NOT_IMPLEMENTED = "agent_not_implemented"


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


class HandoffRecord(BaseModel):
    """A case as the operator endpoints show it."""

    status: Literal["open", "closed"]
    case: HandoffCase
    closed_at: datetime | None = None


class OperatorInput(BaseModel):
    """The operator's decision: send the proposed draft, or answer in their own words."""

    action: Literal["approve", "reply"]
    answer: str | None = None

    @model_validator(mode="after")
    def check_answer(self) -> "OperatorInput":
        """A reply needs words; an approval takes none, the draft is the answer."""
        if self.action == "reply" and not (self.answer and self.answer.strip()):
            raise ValueError("a reply needs a non-empty answer")
        if self.action == "approve" and self.answer is not None:
            raise ValueError("an approval sends the proposed draft and takes no answer")
        return self


CONVERSATION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class ChatRequest(BaseModel):
    """Inbound payload. `user_id` is taken as already authenticated."""

    message: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    conversation_id: str | None = Field(default=None, pattern=CONVERSATION_ID_PATTERN)
    """Continues a conversation when given; the server starts a new one when absent. It only
    names a thread together with `user_id`, never on its own."""


class ChatResponse(BaseModel):
    """Outbound payload. `answer` is always Brazilian Portuguese."""

    conversation_id: str
    status: Literal["answered", "pending_human"] = "answered"
    """`pending_human` while an operator has the case; the answer is then a handoff notice."""
    answer: str
    intent: Intent
    agents: list[TargetAgent] = Field(default_factory=list)
    """The specialists that ran, in plan order. Empty when routing never reached one."""
    confidence: float = 0.0
    sources: list[Source] = Field(default_factory=list)
    escalated: bool = False
    escalation: Escalation | None = None

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
