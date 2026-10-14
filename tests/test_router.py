"""Router behaviour, with the LLM provider mocked. Runs offline."""

import pytest

from app.contracts import Intent, TargetAgent
from app.graph.router import RouterDecision, route
from app.graph.state import GraphState
from app.providers.llm import LLMProviderError


class StubProvider:
    """Returns a canned decision, or raises, without touching the network."""

    def __init__(self, decision: RouterDecision | None = None, error: str | None = None) -> None:
        self._decision = decision
        self._error = error

    async def structured(self, *, system: str, user: str, schema: type) -> RouterDecision:
        if self._error is not None:
            raise LLMProviderError(self._error)
        assert self._decision is not None
        return self._decision


async def test_product_question_in_english_goes_to_knowledge() -> None:
    """A product comparison needs no account data, and carries a pt-BR query for retrieval."""
    decision = RouterDecision(
        intent=Intent.PRODUCT_INFO,
        target_agent=TargetAgent.KNOWLEDGE,
        confidence=0.93,
        rationale="comparação de produtos publicada no site",
        lang="en",
        query_pt="diferença entre a maquininha Get Clássica e a Get Smart",
    )
    state = GraphState(
        message="What's the difference between the Get Clássica and the Get Smart?",
        user_id="cliente1988",
    )

    update = await route(state, StubProvider(decision), window=6)

    assert update["route"].target_agent is TargetAgent.KNOWLEDGE
    assert update["route"].query_pt.startswith("diferença")
    assert update["hops"] == 1


async def test_account_question_goes_to_support() -> None:
    """Answering requires reading this merchant's settlement data."""
    decision = RouterDecision(
        intent=Intent.ACCOUNT_DATA,
        target_agent=TargetAgent.SUPPORT,
        confidence=0.88,
        rationale="depende da agenda de recebíveis do cliente",
        lang="pt",
        query_pt="quando cai o dinheiro da venda de ontem",
    )
    state = GraphState(message="Quando cai o dinheiro da venda de ontem?", user_id="cliente1988")

    update = await route(state, StubProvider(decision), window=6)

    assert update["route"].target_agent is TargetAgent.SUPPORT
    assert update["route"].intent is Intent.ACCOUNT_DATA


async def test_provider_failure_becomes_zero_confidence_not_an_exception() -> None:
    """The failure collapses into the value the threshold edge already handles."""
    state = GraphState(message="Quando cai o dinheiro?", user_id="cliente1988")

    update = await route(state, StubProvider(error="upstream timeout"), window=6)

    assert update["route"].confidence == 0.0
    assert update["route"].query_pt == state.message
    assert "upstream timeout" in update["route"].rationale


def test_confidence_outside_the_unit_interval_is_rejected() -> None:
    """The decision is a validated contract, not free-form text."""
    with pytest.raises(ValueError):
        RouterDecision(
            intent=Intent.OTHER,
            target_agent=TargetAgent.KNOWLEDGE,
            confidence=1.4,
            rationale="fora do intervalo",
        )
