"""The support agent.

Two structured steps instead of a vendor tool-calling loop: the model picks an action, the code
executes it, and a second call turns the result into prose. The identity never passes through the
model in either step, and the plan is a validated object rather than parsed text.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from app.contracts import EscalationReason, Source, SourceType
from app.prompts import load_prompt
from app.providers.llm import LLMProvider, LLMProviderError
from app.tools.account import DateField, call_tool

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
    date_field: DateField | None = None
    """For the settlement schedule: filter by the sale date or by the credit date."""
    status: str | None = None
    limit: int = 10

    def arguments(self, today: date | None = None) -> dict[str, Any]:
        """Only the fields the chosen tool accepts. `user_id` is not among them by construction.

        A sale date after today cannot have sales yet, so "the money from tomorrow's sales" is
        read as the money crediting tomorrow.
        """
        if self.tool != "get_settlement_schedule":
            return {"status": self.status, "limit": self.limit}
        date_from, date_to = self.date_from or "", self.date_to or ""
        field = self.date_field or "credit_date"
        if field == "sale_date" and today is not None and date_from > today.isoformat():
            field = "credit_date"
        return {"date_from": date_from, "date_to": date_to, "date_field": field}


def unexplained_decline(plan: SupportPlan, result: dict[str, Any]) -> bool:
    """A question about declined sales that the data cannot answer: none is on record, or none
    carries the reason the issuer gave. Only then does a decline need a person."""
    if plan.tool != "get_transactions" or plan.status != "negada":
        return False
    declined = [item for item in result.get("transactions", []) if item["status"] == "negada"]
    return not any(item.get("decline_reason") for item in declined)


class SupportAnswer(BaseModel):
    """The second structured step: prose grounded in the tool result."""

    answer: str


def planning_input(state: GraphState, question: str) -> str:
    """The planner's input: the standalone question and the date of the turn, which is what turns
    "yesterday" or "tomorrow" into a date range. Never the conversation history."""
    today = state.today.isoformat() if state.today else None
    return json.dumps({"today": today, "question": question}, ensure_ascii=False)


def answer_input(
    state: GraphState, question: str, tool: str, arguments: dict[str, Any], result: dict[str, Any]
) -> str:
    """What the answer step reads: today, the question, the query as it was run, and its result.
    The query matters when it differs from the words of the question, as when "tomorrow's sales"
    was read as the money crediting tomorrow."""
    today = state.today.isoformat() if state.today else "não informada"
    return (
        f"Hoje: {today}\n\nPergunta do cliente:\n{question}\n\n"
        f"Consulta feita: {tool} {json.dumps(arguments, ensure_ascii=False)}\n\n"
        f"Resultado da consulta {tool}:\n{json.dumps(result, ensure_ascii=False)}"
    )


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

    arguments = plan.arguments(state.today)
    result = call_tool(plan.tool, arguments, state.user_id, state.today)
    evidence = [*state.evidence, json.dumps(result, ensure_ascii=False)]
    if unexplained_decline(plan, result):
        return {"refusal_reason": EscalationReason.INDIVIDUAL_CASE.value, "evidence": evidence}
    try:
        generated = await provider.structured(
            system=load_prompt("support_answer").text,
            user=answer_input(state, question, plan.tool, arguments, result),
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
