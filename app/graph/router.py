"""The router node and its structured output.

The router is the only agent that reads the conversation history. It uses it for one thing:
rewriting the current message into a standalone question (`query_pt`), so the specialists stay
stateless and never see an earlier turn.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from app.contracts import Intent, TargetAgent
from app.observability import get_logger
from app.prompts import load_prompt
from app.providers.llm import LLMProvider, LLMProviderError

if TYPE_CHECKING:
    from app.graph.state import GraphState

logger = logging.getLogger(__name__)
events = get_logger(__name__)

_IDENTIFIER = re.compile(r"[^\W_]*\d[^\W_]*")
"""Any alphanumeric token with a digit in it: amounts, dates, protocols, merchant ids, cards."""


class RouterDecision(BaseModel):
    """The router's only output. Nothing downstream parses model prose."""

    intent: Intent
    target_agent: TargetAgent
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    lang: Literal["pt", "en", "other"] = "pt"
    query_pt: str = ""
    """The current message as a standalone Portuguese question: translated when needed, with
    references to earlier turns resolved. Specialists see this, never the history."""


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


def router_input(state: GraphState, window: int) -> str:
    """The router's user message: the current message, plus earlier turns as data when any exist.

    History travels in its own JSON field, never spliced into the instructions, so text from an
    earlier turn can only ever be read as something the merchant said.
    """
    payload: dict[str, Any] = {"message": state.message}
    recent = state.messages[-window:] if window else []
    if recent or state.summary:
        payload["history"] = [{"role": item.type, "content": item.content} for item in recent]
        payload["summary"] = state.summary
    if state.handoff_reason:
        payload["handed_back_by_specialist"] = True
    return json.dumps(payload, ensure_ascii=False)


def invented_identifiers(rewrite: str, conversation: str) -> set[str]:
    """Identifier-like tokens in the rewrite that appear nowhere in the conversation."""
    known = {token.lower() for token in _IDENTIFIER.findall(conversation)}
    return {token for token in map(str.lower, _IDENTIFIER.findall(rewrite)) if token not in known}


def _grounded(decision: RouterDecision, state: GraphState, window: int) -> RouterDecision:
    """Keep the rewrite only if every identifier in it came from the conversation.

    A rewrite that introduces a number, date, protocol or merchant id is the model inventing
    one; the original message is the safer query.
    """
    if not decision.query_pt:
        return decision.model_copy(update={"query_pt": state.message})
    conversation = " ".join(
        [state.message, state.summary, *(str(item.content) for item in state.messages[-window:])]
    )
    invented = invented_identifiers(decision.query_pt, conversation)
    if not invented:
        return decision
    events.warning("router.rewrite_rejected", invented_count=len(invented))
    return decision.model_copy(update={"query_pt": state.message})


async def route(state: GraphState, provider: LLMProvider, window: int) -> dict[str, Any]:
    """Classify the message, rewrite it into a standalone query, and record both on the state."""
    try:
        decision = await provider.structured(
            system=load_prompt("router").text,
            user=router_input(state, window),
            schema=RouterDecision,
        )
    except LLMProviderError as exc:
        logger.warning("router call failed, falling back to zero confidence: %s", exc)
        decision = _failed_decision(state.message, str(exc))

    decision = _grounded(decision, state, window)
    events.info(
        "router.rewrite",
        message=state.masked_message or state.message,
        standalone_query=decision.query_pt,
    )
    return {"route": decision, "hops": state.hops + 1, "handoff_reason": None}
