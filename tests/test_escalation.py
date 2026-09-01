"""The escalation node: deterministic handoff, typed reason."""

from app.contracts import EscalationReason
from app.graph.escalation import escalate, mint_protocol, resolve_reason
from app.graph.state import GraphState


def _state(**fields) -> GraphState:
    return GraphState(message="minha venda foi negada", user_id="cliente1988", **fields)


def test_protocol_is_unique_and_quotable() -> None:
    """The merchant needs something to say to a human agent."""
    first, second = mint_protocol(), mint_protocol()

    assert first.startswith("ESC-") and len(first) == 14
    assert first != second


def test_a_refusal_outranks_a_handoff() -> None:
    """A specialist that must not answer is a stronger signal than one that passed the ball."""
    state = _state(
        refusal_reason=EscalationReason.INDIVIDUAL_CASE.value,
        handoff_reason=EscalationReason.REPEATED_MISROUTE.value,
    )

    assert resolve_reason(state) is EscalationReason.INDIVIDUAL_CASE


def test_no_reason_means_routing_never_got_anywhere() -> None:
    """Reaching escalation with nothing set can only be the confidence edge."""
    assert resolve_reason(_state()) is EscalationReason.LOW_ROUTER_CONFIDENCE


async def test_escalation_payload_carries_reason_and_masked_summary() -> None:
    """What a human receives: a protocol, a typed reason, and no raw PII."""
    state = _state(
        refusal_reason=EscalationReason.INDIVIDUAL_CASE.value,
        masked_message="meu cpf é [CPF], minha venda foi negada",
    )

    update = await escalate(state)

    escalation = update["escalation"]
    assert escalation.reason is EscalationReason.INDIVIDUAL_CASE
    assert escalation.protocol in update["answer"]
    assert "[CPF]" in escalation.summary
    assert update["sources"] == []


async def test_escalation_runs_without_a_model() -> None:
    """No provider is passed in, because none is needed."""
    update = await escalate(_state())

    assert update["answer"].strip()
    assert update["escalation"].reason is EscalationReason.LOW_ROUTER_CONFIDENCE
