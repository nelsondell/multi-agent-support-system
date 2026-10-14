"""The support agent.

Two structured steps instead of a vendor tool-calling loop: the model picks an action, the code
executes it, and a second call turns the result into prose. The identity never passes through the
model in either step, and the plan is a validated object rather than parsed text.
"""

from __future__ import annotations

import json
import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from app.contracts import EscalationReason, Source, SourceType
from app.prompts import load_prompt
from app.providers.llm import LLMProvider, LLMProviderError
from app.tools.account import call_tool

if TYPE_CHECKING:
    from app.graph.state import GraphState

logger = logging.getLogger(__name__)

ToolName = Literal["get_settlement_schedule", "get_transactions"]


class SupportAction(StrEnum):
    """What the agent decided to do with the message."""

    CALL_TOOL = "call_tool"
    HANDOFF_TO_KNOWLEDGE = "handoff_to_knowledge"
    ESCALATE_INDIVIDUAL_CASE = "escalate_individual_case"


class SupportPlan(BaseModel):
    """The first structured step: which action, and the arguments the model may choose."""

    action: SupportAction
    tool: ToolName | None = None
    date_from: str | None = None
    date_to: str | None = None
    status: str | None = None
    limit: int = 10

    def arguments(self) -> dict[str, Any]:
        """Only the fields the chosen tool accepts. `user_id` is not among them by construction."""
        if self.tool == "get_settlement_schedule":
            return {"date_from": self.date_from or "", "date_to": self.date_to or ""}
        return {"status": self.status, "limit": self.limit}


class SupportAnswer(BaseModel):
    """The second structured step: prose grounded in the tool result."""

    answer: str


def planning_input(state: GraphState, question: str) -> str:
    """The planner's input: the standalone question and the date of the turn, which is what turns
    "yesterday" or "tomorrow" into a date range. Never the conversation history."""
    today = state.today.isoformat() if state.today else None
    return json.dumps({"today": today, "question": question}, ensure_ascii=False)


async def answer(state: GraphState, provider: LLMProvider) -> dict[str, Any]:
    """Plan, call one tool, and answer from its result."""
    question = state.current_query()
    try:
        plan = await provider.structured(
            system=load_prompt("support_plan").text,
            user=planning_input(state, question),
            schema=SupportPlan,
        )
    except LLMProviderError as exc:
        logger.warning("support planning failed: %s", exc)
        return {"refusal_reason": EscalationReason.AGENT_NOT_IMPLEMENTED.value}

    if plan.action is SupportAction.ESCALATE_INDIVIDUAL_CASE:
        return {"refusal_reason": EscalationReason.INDIVIDUAL_CASE.value}
    if plan.action is SupportAction.HANDOFF_TO_KNOWLEDGE or plan.tool is None:
        return {"handoff_reason": EscalationReason.REPEATED_MISROUTE.value}

    result = call_tool(plan.tool, plan.arguments(), state.user_id)
    evidence = [*state.evidence, json.dumps(result, ensure_ascii=False)]
    try:
        generated = await provider.structured(
            system=load_prompt("support_answer").text,
            user=(
                f"Pergunta do cliente:\n{question}\n\n"
                f"Resultado da consulta {plan.tool}:\n{json.dumps(result, ensure_ascii=False)}"
            ),
            schema=SupportAnswer,
        )
    except LLMProviderError as exc:
        logger.warning("support generation failed: %s", exc)
        return {
            "refusal_reason": EscalationReason.AGENT_NOT_IMPLEMENTED.value,
            "evidence": evidence,
        }

    account = Source(source_type=SourceType.ACCOUNT, title=plan.tool)
    return {"answer": generated.answer, "sources": [account], "evidence": evidence}
