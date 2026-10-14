"""The join point after the last plan step.

One answered step is copied as it is, with no model call, which is how a single-intent turn
keeps its old path and cost. Two answered steps are merged by one structured call, and the merge
may not carry a number the step answers lack (hard rule 4); if it does, or the call fails, the
step answers are joined as written. Sources are the union of every answered step's, never
something the merge chose.

When a step needs a human, the answered part goes out now and the turn escalates with the
refused step's reason, so the rest reaches `human_review` in the same turn under one protocol.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from app.contracts import Source
from app.graph.numbers import adds_numbers
from app.prompts import load_prompt
from app.providers.llm import LLMProvider, LLMProviderError

if TYPE_CHECKING:
    from app.graph.state import GraphState, StepResult

logger = logging.getLogger(__name__)


class ComposedAnswer(BaseModel):
    """Two step answers, merged into one reply."""

    answer: str


def every_source(results: list[StepResult]) -> list[Source]:
    """The union of the steps' sources, in step order, without repeats."""
    union: list[Source] = []
    for result in results:
        union.extend(source for source in result.sources if source not in union)
    return union


def joined(results: list[StepResult]) -> str:
    """The step answers as written, one paragraph each."""
    return "\n\n".join(result.answer for result in results if result.answer)


async def _merge(state: GraphState, answered: list[StepResult], provider: LLMProvider) -> str:
    """One model call merges the answers; their own text wins if the merge adds a number."""
    payload = {
        "question": state.message,
        "parts": [{"question": item.query_pt, "answer": item.answer} for item in answered],
    }
    try:
        merged = await provider.structured(
            system=load_prompt("compose").text,
            user=json.dumps(payload, ensure_ascii=False),
            schema=ComposedAnswer,
        )
    except LLMProviderError as exc:
        logger.warning("compose failed, joining the step answers: %s", exc)
        return joined(answered)
    text = merged.answer.strip()
    if not text or adds_numbers(text, [item.answer for item in answered]):
        logger.warning("composed answer rejected, joining the step answers")
        return joined(answered)
    return text


async def compose(state: GraphState, provider: LLMProvider) -> dict[str, Any]:
    """Turn the step results into the turn's answer, or into an answered part and a handoff."""
    answered = [result for result in state.step_results if not result.refusal_reason]
    refused = [result for result in state.step_results if result.refusal_reason]
    if len(answered) > 1:
        text = await _merge(state, answered, provider)
    else:
        text = answered[0].answer if answered else None
    update: dict[str, Any] = {"sources": every_source(answered), "answer": text}
    if refused:
        update["refusal_reason"] = refused[0].refusal_reason
        update["partial_answer"] = text
        update["answer"] = text or joined(refused) or None
    return update
