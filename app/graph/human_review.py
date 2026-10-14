"""The fourth agent: a human in the loop.

It is two nodes because `interrupt()` re-runs its whole node on resume. `prepare_handoff` does
every side effect, once: it drafts a reply with one model call and records the case in the
queue, using the protocol `escalate` already minted. `await_operator` reads the state and pauses,
nothing else. When the operator answers, the same node turns their input into the merchant's
reply and the graph goes on to `remember`, so the next turn can build on what the human said.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from langgraph.types import interrupt
from pydantic import BaseModel

from app.contracts import EscalationReason, HandoffCase, OperatorInput, Source, SourceType
from app.graph.memory import thread_id
from app.graph.numbers import adds_numbers
from app.observability import mask_pii
from app.prompts import load_prompt
from app.providers.llm import LLMProvider, LLMProviderError

if TYPE_CHECKING:
    from app.graph.state import GraphState
    from app.handoffs import HandoffQueue

logger = logging.getLogger(__name__)

STOPPED_BECAUSE = {
    EscalationReason.LOW_ROUTER_CONFIDENCE: "router confidence was below the threshold",
    EscalationReason.REPEATED_MISROUTE: "both specialists declined the request",
    EscalationReason.LOW_RETRIEVAL_CONFIDENCE: "no retrieved passage was close enough to answer",
    EscalationReason.INSUFFICIENT_SOURCES: "the sources found do not answer the question",
    EscalationReason.INDIVIDUAL_CASE: "an individual case, which only a person may decide",
    EscalationReason.TOOL_UNAVAILABLE: "the account data could not be read",
    EscalationReason.AGENT_NOT_IMPLEMENTED: "a model call failed before the specialist finished",
}


class HandoffDraft(BaseModel):
    """A reply the operator may send as is."""

    draft: str


class OperatorReply(BaseModel):
    """The operator's answer, rephrased for the merchant."""

    answer: str


def attempts(state: GraphState) -> list[str]:
    """What the agents tried and why it stopped, from what the state recorded."""
    lines = []
    if state.route is not None:
        plan = " then ".join(step.agent.value for step in state.route.steps)
        lines.append(
            f"router: {state.route.intent.value}, plan {plan}, "
            f"confidence {state.route.confidence:.2f}"
        )
    if state.hops > 1:
        lines.append(f"router: handed back by a specialist, {state.hops} router passes")
    for number, result in enumerate(state.step_results, start=1):
        outcome = "answered"
        if result.refusal_reason:
            outcome = f"stopped: {STOPPED_BECAUSE[EscalationReason(result.refusal_reason)]}"
        lines.append(
            f"step {number} ({result.agent.value}) {outcome}; query: {mask_pii(result.query_pt)}"
        )
    if state.consulted:
        lines.append(f"knowledge: {len(state.consulted)} pages retrieved")
    if state.escalation is not None and not state.step_results:
        lines.append(f"stopped: {STOPPED_BECAUSE[state.escalation.reason]}")
    return lines


async def _draft(state: GraphState, provider: LLMProvider) -> str | None:
    """One structured call proposes a reply. None when it fails, or when it states a number that
    is in none of the case's evidence, since an approved draft goes out exactly as written."""
    question = state.escalation.summary
    payload = {
        "question": question,
        "intent": state.route.intent.value if state.route else None,
        "reason": state.escalation.reason.value,
        "pages": [source.title for source in state.consulted],
        "evidence": state.evidence,
    }
    try:
        drafted = await provider.structured(
            system=load_prompt("handoff_draft").text,
            user=json.dumps(payload, ensure_ascii=False),
            schema=HandoffDraft,
        )
    except LLMProviderError as exc:
        logger.warning("handoff draft failed, the case opens without one: %s", exc)
        return None
    draft = drafted.draft.strip()
    invented = adds_numbers(draft, [question, *state.evidence], quoting=[state.escalation.protocol])
    return draft if draft and not invented else None


async def prepare_handoff(
    state: GraphState, provider: LLMProvider, queue: HandoffQueue | None
) -> dict[str, Any]:
    """Build the operator's case and record it, before anything pauses."""
    escalation = state.escalation
    case = HandoffCase(
        protocol=escalation.protocol,
        reason=escalation.reason,
        intent=state.route.intent if state.route else None,
        summary=escalation.summary,
        attempts=attempts(state),
        sources=state.consulted,
        proposed_draft=await _draft(state, provider),
        user_id=state.user_id,
        conversation_id=state.conversation_id,
        opened_at=datetime.now(UTC),
    )
    if queue is not None:
        await queue.open(case, thread_id(state.user_id, state.conversation_id))
    return {"case": case}


async def _rephrase(answer: str, case: HandoffCase, provider: LLMProvider) -> str:
    """Word the operator's answer for the merchant. Their literal text wins whenever the
    rephrasing fails or carries a number they did not write (hard rule 4)."""
    payload = {"question": case.summary, "operator_answer": answer}
    try:
        reply = await provider.structured(
            system=load_prompt("operator_reply").text,
            user=json.dumps(payload, ensure_ascii=False),
            schema=OperatorReply,
        )
    except LLMProviderError as exc:
        logger.warning("operator reply rephrasing failed, sending it verbatim: %s", exc)
        return answer
    text = reply.answer.strip()
    if not text or adds_numbers(text, [answer], quoting=[case.protocol]):
        logger.warning("rephrased operator reply rejected, sending it verbatim")
        return answer
    return text


async def await_operator(state: GraphState, provider: LLMProvider) -> dict[str, Any]:
    """Pause until an operator decides, then answer the merchant from that decision only."""
    case = state.case
    decision = OperatorInput.model_validate(interrupt(case.model_dump(mode="json")))
    if decision.action == "approve":
        if not case.proposed_draft:
            raise ValueError(f"case {case.protocol} has no draft to approve")
        reply = case.proposed_draft
    else:
        reply = await _rephrase(decision.answer, case, provider)
    operator = Source(source_type=SourceType.OPERATOR, title=case.protocol)
    return {"answer": reply, "sources": [operator]}
