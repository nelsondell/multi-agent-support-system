"""API and cross-node contracts.

`user_id` appears in the request envelope only. It is never a field of a model the LLM can read
or write, which is what keeps authorization out of prompt space.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


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


class ChatRequest(BaseModel):
    """Inbound payload. `user_id` is taken as already authenticated."""

    message: str = Field(min_length=1)
    user_id: str = Field(min_length=1)


class ChatResponse(BaseModel):
    """Outbound payload. `answer` is always Brazilian Portuguese."""

    answer: str
    intent: Intent
    target_agent: TargetAgent | None = None
    confidence: float = 0.0
    sources: list[Source] = Field(default_factory=list)
    escalated: bool = False
    escalation: Escalation | None = None
