"""The knowledge agent: grounded answers, and refusal when the corpus does not answer."""

import pytest

from app.contracts import EscalationReason, Intent, TargetAgent
from app.graph.knowledge import KnowledgeAnswer, answer, format_context
from app.graph.router import RouterDecision
from app.graph.state import GraphState
from app.providers.llm import LLMProviderError
from app.rag.retrieve import Hit, RetrievalResult


class StubProvider:
    """Returns a canned answer, or raises. Records what it was asked."""

    def __init__(self, answer: KnowledgeAnswer | None = None, error: str | None = None) -> None:
        self._answer = answer
        self._error = error
        self.calls: list[str] = []

    async def structured(self, *, system: str, user: str, schema: type) -> KnowledgeAnswer:
        self.calls.append(user)
        if self._error is not None:
            raise LLMProviderError(self._error)
        assert self._answer is not None
        return self._answer


def _state(message: str = "como funciona a antecipação?") -> GraphState:
    state = GraphState(message=message, user_id="cliente1988")
    state.route = RouterDecision(
        intent=Intent.PRODUCT_INFO,
        target_agent=TargetAgent.KNOWLEDGE,
        confidence=0.9,
        rationale="produto publicado",
        lang="pt",
        query_pt=message,
    )
    return state


def _result(chunks, similarity: float) -> RetrievalResult:
    hits = [
        Hit(chunk=chunk, rrf_score=0.03, dense_similarity=similarity, bm25_rank=1, dense_rank=1)
        for chunk in chunks
    ]
    return RetrievalResult(hits=hits, confidence=similarity)


async def test_answer_cites_only_retrieved_urls(sample_chunks) -> None:
    """Sources come from what was retrieved, never from what the model claims."""
    chunks = sample_chunks[:2]
    provider = StubProvider(
        KnowledgeAnswer(
            answer="A antecipação adianta o valor das suas vendas a prazo.",
            used_urls=[chunks[0].url, "https://exemplo-inventado.com/"],
        )
    )

    update = await answer(_state(), provider, _result(chunks, 0.8), threshold=0.35)

    urls = [source.url for source in update["sources"]]
    assert chunks[0].url in urls
    assert "https://exemplo-inventado.com/" not in urls
    assert "refusal_reason" not in update


async def test_below_threshold_refuses_without_calling_the_model(sample_chunks) -> None:
    """No retrieval confidence means no generation at all, not a hedged answer."""
    provider = StubProvider(KnowledgeAnswer(answer="não deveria ser gerado", used_urls=[]))

    update = await answer(
        _state("cotação do euro"), provider, _result(sample_chunks[:1], 0.05), 0.35
    )

    assert provider.calls == []
    assert update["refusal_reason"] == EscalationReason.LOW_RETRIEVAL_CONFIDENCE.value
    assert update["sources"] == []
    assert "não encontrei" in update["answer"].lower()


async def test_generation_failure_refuses_instead_of_raising(sample_chunks) -> None:
    """A vendor error becomes a handoff, never a stack trace."""
    provider = StubProvider(error="upstream 503")

    update = await answer(_state(), provider, _result(sample_chunks[:1], 0.9), threshold=0.35)

    assert update["refusal_reason"] == EscalationReason.LOW_RETRIEVAL_CONFIDENCE.value


def test_context_labels_every_chunk_with_its_url(sample_chunks) -> None:
    """The prompt can only cite urls it was shown."""
    context = format_context(_result(sample_chunks[:2], 0.8).hits)

    assert context.count("url: ") == 2
    assert sample_chunks[0].url in context


@pytest.mark.parametrize("threshold", [0.35, 0.9])
async def test_retrieved_query_is_the_routers_portuguese_query(sample_chunks, threshold) -> None:
    """Retrieval and generation run on query_pt, not on the raw English message."""
    provider = StubProvider(KnowledgeAnswer(answer="ok", used_urls=[]))
    state = _state()
    state.route = state.route.model_copy(update={"query_pt": "antecipação de recebíveis"})

    await answer(state, provider, _result(sample_chunks[:1], 0.95), threshold)

    assert "antecipação de recebíveis" in provider.calls[0]
