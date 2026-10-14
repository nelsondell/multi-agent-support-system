"""API and cross-node contracts.

`user_id` appears in the request envelope only. It is never a field of a model the LLM can read
or write, which is what keeps authorization out of prompt space.
"""

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class Intent(StrEnum):
    """What the merchant is asking for, independent of which agent serves it."""

    PRODUCT_INFO = "product_info"
    TROUBLESHOOTING = "troubleshooting"
    ACCOUNT_DATA = "account_data"
    INDIVIDUAL_CASE = "individual_case"
    OTHER = "other"


class TargetAgent(StrEnum):
    """The specialist a decision points to."""

    KNOWLEDGE = "knowledge"
    SUPPORT = "support"


class GuardrailOutcome(StrEnum):
    """What the guardrail decided before routing."""

    ALLOW = "allow"
    OUT_OF_DOMAIN = "out_of_domain"
    IDENTITY_SWAP = "identity_swap"


class EscalationReason(StrEnum):
    """Why a case left the automated path. Doubles as the operational metric."""

    LOW_ROUTER_CONFIDENCE = "low_router_confidence"
    REPEATED_MISROUTE = "repeated_misroute"
    LOW_RETRIEVAL_CONFIDENCE = "low_retrieval_confidence"
    INDIVIDUAL_CASE = "individual_case"
    AGENT_NOT_IMPLEMENTED = "agent_not_implemented"


class Source(BaseModel):
    """A corpus page backing an answer."""

    url: str
    title: str


class Escalation(BaseModel):
    """Structured handoff payload, built without an LLM call."""

    protocol: str
    reason: EscalationReason
    summary: str


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
    answer: str
    intent: Intent
    target_agent: TargetAgent | None = None
    confidence: float = 0.0
    sources: list[Source] = Field(default_factory=list)
    escalated: bool = False
    escalation: Escalation | None = None

    @model_validator(mode="after")
    def check_consistency(self) -> "ChatResponse":
        """Reject responses that say two different things about what happened."""
        if self.escalated != (self.escalation is not None):
            raise ValueError("escalated must match the presence of an escalation payload")
        if self.escalated and self.sources:
            raise ValueError("an escalated response cites no sources: it answered nothing")
        if not self.escalated and not self.answer.strip():
            raise ValueError("a non-escalated response must carry an answer")
        if (
            self.escalation is not None
            and self.escalation.reason is EscalationReason.LOW_ROUTER_CONFIDENCE
            and self.target_agent is not None
        ):
            raise ValueError("routing never reached a specialist, so none may be reported")
        return self
