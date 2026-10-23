"""The router node and its structured output: a plan of one or two specialists.

The router is the only agent that reads the conversation history. It uses it for one thing:
rewriting the current message into standalone questions, one per plan step, so the specialists
stay stateless and never see an earlier turn.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from app.contracts import Intent, TargetAgent
from app.observability import get_logger
from app.prompts import load_prompt
from app.providers.llm import LLMProvider

if TYPE_CHECKING:
    from app.graph.state import GraphState

events = get_logger(__name__)

_IDENTIFIER = re.compile(r"[^\W_]*\d[^\W_]*")
"""Any alphanumeric token with a digit in it: amounts, dates, protocols, merchant ids, cards."""


class PlanStep(BaseModel):
    """One specialist and the part of the request it answers."""

    agent: TargetAgent
    query_pt: str = ""
    """That part as a standalone Portuguese question: translated when needed, with references
    to earlier turns resolved. The specialist sees this, never the history."""
    search_queries: list[str] = Field(default_factory=list, max_length=3)
    """For a knowledge step that compares or combines products: one short search per item, such
    as "maquininha Get Smart". Retrieval runs them beside `query_pt`. Empty otherwise."""
    target_day: Literal["yesterday", "today", "tomorrow"] | None = None
    """For a general-information step whose answer depends on the day, such as a forecast or an
    exchange rate: the day asked about. The web query then carries that day's date."""
    requested_by: SkipJsonSchema[TargetAgent | None] = None
    """Set by the supervisor, never by a model, and absent from the router's schema: the
    specialist whose finding added this step. Such a step only reads, never goes to the web, and
    is dropped rather than escalated when it does not answer."""


class RouterDecision(BaseModel):
    """The router's only output. Nothing downstream parses model prose."""

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    lang: Literal["pt", "en", "other"] = "pt"
    steps: list[PlanStep] = Field(min_length=1, max_length=2)
    """Run in order. Two steps only for a genuinely compound request; the schema refuses three."""


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


def _grounded_step(step: PlanStep, message: str, conversation: str) -> PlanStep:
    """Keep a step's rewrite and searches only where every identifier came from the conversation.

    A rewrite that introduces a number, date, protocol or merchant id is the model inventing
    one; the original message is the safer query, and such a search is simply dropped.
    """
    query = step.query_pt or message
    invented = invented_identifiers(query, conversation)
    if invented:
        events.warning("router.rewrite_rejected", invented_count=len(invented))
        query = message
    searches = [
        search for search in step.search_queries if not invented_identifiers(search, conversation)
    ]
    update = {"query_pt": query, "search_queries": searches, "requested_by": None}
    return step.model_copy(update=update)  # only the supervisor marks a step as added


def _grounded(decision: RouterDecision, state: GraphState, window: int) -> RouterDecision:
    """Apply the identifier check to every step of the plan."""
    conversation = " ".join(
        [state.message, state.summary, *(str(item.content) for item in state.messages[-window:])]
    )
    steps = [_grounded_step(step, state.message, conversation) for step in decision.steps]
    return decision.model_copy(update={"steps": steps})


async def route(state: GraphState, provider: LLMProvider, window: int) -> dict[str, Any]:
    """Plan the turn, rewrite each step into a standalone query, and record both on the state.

    A new plan starts the turn over: earlier step results, from before a hand-back, are dropped,
    except an answer an operator already gave, which no replan may lose.
    """
    decision = await provider.structured(
        system=load_prompt("router").text,
        user=router_input(state, window),
        schema=RouterDecision,
    )
    decision = _grounded(decision, state, window)
    events.info(
        "router.rewrite",
        message=state.masked_message or state.message,
        standalone_query=" | ".join(step.query_pt for step in decision.steps),
        searches=" | ".join(search for step in decision.steps for search in step.search_queries),
    )
    return {
        "route": decision,
        "plan": decision.steps,
        "hops": state.hops + 1,
        "handoff_reason": None,
        "step_index": 0,
        "step_results": [result for result in state.step_results if result.by_operator],
    }
