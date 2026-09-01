"""The router node and its structured output."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from app.contracts import Intent, TargetAgent
from app.prompts import load_prompt
from app.providers.llm import LLMProvider, LLMProviderError

if TYPE_CHECKING:
    from app.graph.state import GraphState

logger = logging.getLogger(__name__)


class RouterDecision(BaseModel):
    """The router's only output. Nothing downstream parses model prose."""

    intent: Intent
    target_agent: TargetAgent
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    lang: Literal["pt", "en", "other"] = "pt"
    query_pt: str = ""


def _failed_decision(message: str, error: str) -> RouterDecision:
    """A failed model call is expressed as a decision nobody trusts.

    Zero confidence is already handled by the threshold edge in `build`, so the graph needs no
    parallel error branch.
    """
    return RouterDecision(
        intent=Intent.OTHER,
        target_agent=TargetAgent.KNOWLEDGE,
        confidence=0.0,
        rationale=f"router unavailable: {error}",
        lang="pt",
        query_pt=message,
    )


async def route(state: GraphState, provider: LLMProvider) -> dict[str, Any]:
    """Classify the message and record the decision on the state."""
    prompt = load_prompt("router")
    try:
        decision = await provider.structured(
            system=prompt.text, user=state.message, schema=RouterDecision
        )
    except LLMProviderError as exc:
        logger.warning("router call failed, falling back to zero confidence: %s", exc)
        decision = _failed_decision(state.message, str(exc))

    if not decision.query_pt:
        decision = decision.model_copy(update={"query_pt": state.message})
    return {"route": decision, "hops": state.hops + 1}
