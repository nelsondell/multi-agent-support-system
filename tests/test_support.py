"""Support agent, hop budget and response consistency."""

import json
from datetime import date

import pytest
from pydantic import ValidationError

from app.contracts import (
    ChatResponse,
    Escalation,
    EscalationReason,
    Intent,
    Source,
    SourceType,
    TargetAgent,
)
from app.graph.build import after_specialist
from app.graph.router import PlanStep, RouterDecision
from app.graph.state import GraphState
from app.graph.support import SupportAction, SupportAnswer, SupportPlan, answer
from app.providers.llm import LLMProviderError
from app.settings import Settings

CALLER = "cliente1988"


class ScriptedProvider:
    """Returns the next scripted object per call, recording the prompts it received."""

    def __init__(self, *responses, error: str | None = None) -> None:
        self._responses = list(responses)
        self._error = error
        self.calls: list[str] = []

    async def structured(self, *, system: str, user: str, schema: type):
        self.calls.append(user)
        if self._error is not None:
            raise LLMProviderError(self._error)
        return self._responses.pop(0)


def _state(message: str = "quando cai o dinheiro da venda de ontem?") -> GraphState:
    state = GraphState(message=message, user_id=CALLER)
    state.route = RouterDecision(
        intent=Intent.ACCOUNT_DATA,
        steps=[PlanStep(agent=TargetAgent.SUPPORT, query_pt=message)],
        confidence=0.9,
        rationale="depende da conta",
        lang="pt",
    )
    return state


async def test_tool_result_reaches_the_answer_step() -> None:
    """The agent answers from the caller's own settlement rows."""
    plan = SupportPlan(
        action=SupportAction.CALL_TOOL,
        tool="get_settlement_schedule",
        date_from="2026-09-01",
        date_to="2026-09-30",
    )
    provider = ScriptedProvider(plan, SupportAnswer(answer="A previsão em conta é de R$ 231,26."))

    update = await answer(_state(), provider)

    assert update["answer"].startswith("A previsão")
    assert update["sources"] == [
        Source(source_type=SourceType.ACCOUNT, title="get_settlement_schedule")
    ]
    assert "R$ 231,26" in provider.calls[1]


async def test_individual_case_escalates_even_though_the_data_exists() -> None:
    """The refund is in the fixture; explaining it is still a human's job."""
    provider = ScriptedProvider(SupportPlan(action=SupportAction.ESCALATE_INDIVIDUAL_CASE))

    update = await answer(_state("por que meu estorno foi feito?"), provider)

    assert update["refusal_reason"] == EscalationReason.INDIVIDUAL_CASE.value
    assert len(provider.calls) == 1


async def test_handoff_when_the_question_is_not_about_the_account() -> None:
    """The specialist declines rather than answering outside its boundary."""
    provider = ScriptedProvider(SupportPlan(action=SupportAction.HANDOFF_TO_KNOWLEDGE))

    update = await answer(_state("como funciona a antecipação?"), provider)

    assert update["handoff_reason"] == EscalationReason.REPEATED_MISROUTE.value


async def test_a_planning_failure_raises_for_the_graph_to_retry() -> None:
    """The graph retries the node, then escalates it as model_unavailable (test_resilience)."""
    with pytest.raises(LLMProviderError, match="upstream 500"):
        await answer(_state(), ScriptedProvider(error="upstream 500"))


def test_plan_arguments_never_carry_an_identity() -> None:
    """Whatever the model fills in, the tool arguments hold no user field."""
    plan = SupportPlan(action=SupportAction.CALL_TOOL, tool="get_transactions", status="negada")

    assert "user_id" not in plan.arguments()
    assert "user_id" not in SupportPlan.model_fields


@pytest.mark.parametrize(
    ("hops", "expected"),
    [(1, "router"), (2, "collect"), (3, "collect")],
)
def test_handoff_is_bounded_by_the_hop_budget(hops: int, expected: str) -> None:
    """One replan is allowed; after that the hand-off is recorded as the step's refusal."""
    state = _state()
    state.handoff_reason = EscalationReason.REPEATED_MISROUTE.value
    state.hops = hops

    assert after_specialist(state, Settings(max_hops=2)) == expected


def test_refusal_never_re_routes() -> None:
    """A refusal means the specialist reached the data and must not answer, not that it is lost:
    it is recorded as the step's result, never sent back to the router."""
    state = _state()
    state.refusal_reason = EscalationReason.INDIVIDUAL_CASE.value

    assert after_specialist(state, Settings(max_hops=2)) == "collect"


def test_an_answer_is_recorded_as_the_step_result() -> None:
    """Nothing set means the specialist answered its step."""
    assert after_specialist(_state(), Settings(max_hops=2)) == "collect"


def test_response_cannot_claim_an_answer_and_an_escalation() -> None:
    """The flag and the payload have to agree."""
    with pytest.raises(ValidationError):
        ChatResponse(
            conversation_id="c1", answer="tudo certo", intent=Intent.ACCOUNT_DATA, escalated=True
        )


def _escalation(reason: EscalationReason = EscalationReason.INDIVIDUAL_CASE) -> Escalation:
    return Escalation(protocol="ESC-1", reason=reason, summary="x")


@pytest.mark.parametrize(("status", "escalated"), [("answered", False), ("pending_human", True)])
def test_an_operator_source_needs_an_escalation_the_operator_answered(status, escalated) -> None:
    """An operator can only be cited once a human actually answered the turn."""
    with pytest.raises(ValidationError):
        ChatResponse(
            conversation_id="c1",
            status=status,
            answer="resposta",
            intent=Intent.ACCOUNT_DATA,
            sources=[Source(source_type=SourceType.OPERATOR, title="ESC-1")],
            escalated=escalated,
            escalation=_escalation() if escalated else None,
        )


def test_a_pending_turn_may_cite_the_part_it_already_answered() -> None:
    """One step answered, the other went to a human: the answered part keeps its page."""
    response = ChatResponse(
        conversation_id="c1",
        status="pending_human",
        answer="Sobre a antecipação: ... Vou encaminhar o resto.",
        intent=Intent.ACCOUNT_DATA,
        agents=[TargetAgent.KNOWLEDGE, TargetAgent.SUPPORT],
        sources=[Source(url="https://site.getnet.com.br/", title="Getnet")],
        escalated=True,
        escalation=_escalation(),
    )

    assert response.sources and response.status == "pending_human"


def test_router_level_escalation_cannot_name_a_specialist() -> None:
    """Nobody was reached, so no agent may be reported as having handled it."""
    with pytest.raises(ValidationError):
        ChatResponse(
            conversation_id="c1",
            answer="vou encaminhar",
            intent=Intent.OTHER,
            agents=[TargetAgent.SUPPORT],
            escalated=True,
            escalation=_escalation(EscalationReason.LOW_ROUTER_CONFIDENCE),
        )


TODAY = date(2026, 10, 14)


def _today(state: GraphState) -> GraphState:
    state.today = TODAY
    return state


async def test_yesterdays_sales_answer_with_the_money_crediting_today() -> None:
    """S03: filtered by sale date, the answer step sees R$ 231,26 crediting today, and today."""
    plan = SupportPlan(
        action=SupportAction.CALL_TOOL,
        tool="get_settlement_schedule",
        date_from="2026-10-13",
        date_to="2026-10-13",
        date_field="sale_date",
    )
    provider = ScriptedProvider(plan, SupportAnswer(answer="Previsto para hoje."))

    await answer(_today(_state("quando cai o dinheiro das vendas de ontem?")), provider)

    shown = provider.calls[1]
    entries = json.loads(shown.split("get_settlement_schedule:\n", 1)[1])["entries"]
    assert shown.startswith("Hoje: 2026-10-14")
    assert [(e["credit_date"], e["net"], e["status"]) for e in entries] == [
        ("2026-10-14", "R$ 231,26", "previsto")
    ]


def test_tomorrows_sales_are_read_as_the_money_crediting_tomorrow() -> None:
    """No sale has a date after today, so "the money from tomorrow's sales" is what credits then."""
    plan = SupportPlan(
        action=SupportAction.CALL_TOOL,
        tool="get_settlement_schedule",
        date_from="2026-10-15",
        date_to="2026-10-15",
        date_field="sale_date",
    )

    assert plan.arguments(TODAY)["date_field"] == "credit_date"
    assert plan.arguments(date(2026, 10, 16))["date_field"] == "sale_date"


def test_a_plan_without_a_date_field_filters_by_credit_date() -> None:
    plan = SupportPlan(
        action=SupportAction.CALL_TOOL,
        tool="get_settlement_schedule",
        date_from="2026-10-15",
        date_to="2026-10-15",
    )

    assert plan.arguments(TODAY) == {
        "date_from": "2026-10-15",
        "date_to": "2026-10-15",
        "date_field": "credit_date",
    }


DECLINES = SupportPlan(action=SupportAction.CALL_TOOL, tool="get_transactions", status="negada")


async def test_a_decline_is_explained_with_the_reason_the_issuer_recorded() -> None:
    """S08: the answer step reads the declined sale and the issuer's reason, and answers."""
    provider = ScriptedProvider(DECLINES, SupportAnswer(answer="Saldo ou limite insuficiente."))

    update = await answer(_state("minha maquininha mostra erro de transação negada"), provider)

    assert "refusal_reason" not in update
    shown = provider.calls[1]
    assert "saldo ou limite insuficiente no cartão, segundo o banco emissor" in shown
    assert 'Consulta feita: get_transactions {"status": "negada"' in shown


@pytest.mark.parametrize(
    ("merchant", "why"),
    [("cliente2042", "declined with no reason recorded"), ("cliente7771", "nothing declined")],
)
async def test_a_decline_the_data_cannot_explain_goes_to_a_person(merchant, why) -> None:
    provider = ScriptedProvider(DECLINES)
    state = _state("por que minha venda foi negada?")
    state.user_id = merchant

    update = await answer(state, provider)

    assert update["refusal_reason"] == EscalationReason.INDIVIDUAL_CASE.value, why
    assert len(provider.calls) == 1, "no answer is written for a decline nobody can explain"


@pytest.mark.parametrize(
    ("source_type", "url"),
    [
        (SourceType.KB, None),
        (SourceType.WEB, None),
        (SourceType.ACCOUNT, "https://site.getnet.com.br/"),
        (SourceType.OPERATOR, "https://site.getnet.com.br/"),
    ],
)
def test_pages_are_cited_with_a_url_and_nothing_else_is(source_type, url) -> None:
    """kb and web sources always link their page; account and operator sources never do."""
    with pytest.raises(ValidationError):
        Source(source_type=source_type, url=url, title="title")
