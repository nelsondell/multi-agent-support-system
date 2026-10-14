"""The knowledge agent: grounded answers, and refusal when the corpus does not answer."""

import pytest

from app.contracts import EscalationReason, Intent, TargetAgent
from app.graph.knowledge import KnowledgeAnswer, answer, format_context, retrieve
from app.graph.router import PlanStep, RouterDecision
from app.graph.state import GraphState
from app.prompts import load_prompt
from app.providers.llm import LLMProviderError
from app.rag.retrieve import Hit, HybridRetriever, RetrievalResult
from tests.conftest import UnreachableCollection

CREDIARIO = "https://site.getnet.com.br/crediario/"
GET_TAP = "https://site.getnet.com.br/get-tap/"


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
        steps=[PlanStep(agent=TargetAgent.KNOWLEDGE, query_pt=message)],
        confidence=0.9,
        rationale="produto publicado",
        lang="pt",
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
            coverage="full",
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
    provider = StubProvider(
        KnowledgeAnswer(coverage="full", answer="não deveria ser gerado", used_urls=[])
    )

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
    provider = StubProvider(KnowledgeAnswer(coverage="full", answer="ok", used_urls=[]))
    state = _state()
    step = PlanStep(agent=TargetAgent.KNOWLEDGE, query_pt="antecipação de recebíveis")
    state.route = state.route.model_copy(update={"steps": [step]})

    await answer(state, provider, _result(sample_chunks[:1], 0.95), threshold)

    assert "antecipação de recebíveis" in provider.calls[0]


async def test_excerpts_that_do_not_answer_abstain_instead_of_saying_so(sample_chunks) -> None:
    """ "Not found in the excerpts" is not an answer: the step goes to a human instead."""
    provider = StubProvider(
        KnowledgeAnswer(coverage="none", answer="Não encontrei isso nos trechos.", used_urls=[])
    )

    update = await answer(_state(), provider, _result(sample_chunks[:2], 0.8), threshold=0.35)

    assert update["refusal_reason"] == EscalationReason.INSUFFICIENT_SOURCES.value
    assert update["answer"] == load_prompt("no_knowledge").text
    assert update["sources"] == []
    assert update["consulted"], "the operator still sees what was looked at"


def test_the_steps_item_searches_reach_the_retriever(sample_chunks, fake_collection) -> None:
    state = _state("qual a diferença entre o crediário e o get tap?")
    step = state.route.steps[0].model_copy(update={"search_queries": ["crediário", "get tap"]})
    state.route = state.route.model_copy(update={"steps": [step]})

    result = retrieve(state, HybridRetriever(sample_chunks, fake_collection(), top_k=3))

    assert {CREDIARIO, GET_TAP} <= {hit.chunk.url for hit in result.hits}


def test_an_unreachable_embedder_reads_as_nothing_retrieved(sample_chunks) -> None:
    """The step then abstains below any threshold, instead of raising out of the graph."""
    result = retrieve(_state(), HybridRetriever(sample_chunks, UnreachableCollection(), top_k=5))

    assert result.hits == [] and result.confidence == 0.0


def test_the_prompt_no_longer_asks_for_source_lines() -> None:
    """Sources travel in `used_urls` and the response's `sources`, not inside the text."""
    assert "Fonte: " not in load_prompt("knowledge").text


async def test_excerpts_that_answer_part_of_the_question_are_answered(sample_chunks) -> None:
    """S04: the supported part goes out, with what the site does not cover said plainly."""
    reply = (
        "Para receber pelo Pix é preciso cadastrar uma chave vinculada à conta de recebimento. "
        "O site não informa se essa conta precisa ser de um banco tradicional."
    )
    chunks = sample_chunks[:2]
    provider = StubProvider(
        KnowledgeAnswer(coverage="partial", answer=reply, used_urls=[chunks[0].url])
    )

    update = await answer(_state(), provider, _result(chunks, 0.8), threshold=0.35)

    assert update["answer"] == reply
    assert "refusal_reason" not in update
    assert [source.url for source in update["sources"]] == [chunks[0].url]


class QueryRecorder:
    """A retriever that records what it was asked and finds nothing."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, tuple]] = []

    def search(self, query_pt: str, also=()) -> RetrievalResult:
        self.asked.append((query_pt, tuple(also)))
        return RetrievalResult(hits=[], confidence=0.0)


def test_the_whole_question_is_always_one_of_the_queries() -> None:
    state = _state("diferença entre a maquininha Get Clássica e a Get Smart")
    items = ["maquininha Get Clássica", "maquininha Get Smart"]
    step = state.route.steps[0].model_copy(update={"search_queries": items})
    state.route = state.route.model_copy(update={"steps": [step]})
    recorder = QueryRecorder()

    retrieve(state, recorder)

    assert recorder.asked == [
        ("diferença entre a maquininha Get Clássica e a Get Smart", tuple(items))
    ]
