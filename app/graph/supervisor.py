"""Between steps, code (never a model) may add a read step a finding asks for, drop an added step
that did not answer, or stop a turn that spent its budget (docs/adr/0001-bounded-replanning.md)."""

from typing import Any

from app.contracts import EscalationReason, Need, TargetAgent
from app.graph.router import PlanStep
from app.graph.state import GraphState, StepResult
from app.observability import get_logger
from app.observability.metrics import charge, turn_spend
from app.providers.llm import LLMProvider, SchemaT
from app.settings import Settings

events = get_logger(__name__)


class Metered:
    """Counts every model call against the turn's budget."""

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner

    async def structured(self, *, system: str, user: str, schema: type[SchemaT]) -> SchemaT:
        charge(calls=1)
        return await self._inner.structured(system=system, user=user, schema=schema)


def exhausted(settings: Settings) -> str | None:
    """The budget the turn has reached, if any; outside a metered turn, none."""
    spend = turn_spend()
    if spend is None:
        return None
    if spend.model_calls >= settings.max_model_calls:
        return "max_model_calls"
    if spend.cost_usd >= settings.max_cost_usd:
        return "max_cost_usd"
    return None


def report(budget: str) -> None:
    spend = turn_spend()
    events.warning(
        "budget.exhausted",
        budget=budget,
        model_calls=spend.model_calls if spend else None,
        cost_usd=round(spend.cost_usd, 6) if spend else None,
    )


def dropped(state: GraphState) -> bool:
    """An added step that did not answer from a read: the answer stands without it."""
    step = state.current_step()
    if step is None or step.requested_by is None:
        return False
    failed = state.refusal_reason or state.handoff_reason or state.pending_action
    return bool(failed or not state.answer)


def _ignored(state: GraphState, need: Need, agent: TargetAgent, settings: Settings) -> str | None:
    if state.refusal_reason or state.handoff_reason or not state.answer:
        return "step_did_not_answer"
    if need.agent is agent:
        return "same_agent"
    if state.plan_changes >= settings.max_plan_changes:
        return "max_plan_changes"
    if any(step.agent is need.agent and step.query_pt == need.query_pt for step in state.steps):
        return "repeated_step"
    return exhausted(settings)


def revise(state: GraphState, settings: Settings) -> dict[str, Any]:
    """The plan with the need's read step right after the current one."""
    need, step = state.need, state.current_step()
    if need is None or step is None:
        return {}
    ignored = _ignored(state, need, step.agent, settings)
    if ignored:
        events.info("plan.need_ignored", reason=ignored, agent=need.agent.value)
        return {}
    added = PlanStep(agent=need.agent, query_pt=need.query_pt, requested_by=step.agent)
    at = state.step_index + 1
    events.info("plan.step_added", agent=need.agent.value, requested_by=step.agent.value)
    return {
        "plan": [*state.steps[:at], added, *state.steps[at:]],
        "plan_changes": state.plan_changes + 1,
    }


def halt(plan: list[PlanStep], at: int, results: list[StepResult], settings: Settings) -> dict:
    """With a budget spent, run no more steps; the router's next one goes to a person."""
    remaining = plan[at:]
    budget = exhausted(settings) if remaining else None
    if budget is None:
        return {}
    report(budget)
    update: dict[str, Any] = {"step_index": len(plan)}
    planned = [step for step in remaining if step.requested_by is None]
    if planned:
        reason = EscalationReason.BUDGET_EXHAUSTED.value
        stopped = StepResult(
            agent=planned[0].agent, query_pt=planned[0].query_pt, refusal_reason=reason
        )
        update["step_results"] = [*results, stopped]
    return update
