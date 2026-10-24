"""Hybrid retrieval and rank fusion, offline against the sampled knowledge base."""

import pytest

from app.rag.retrieve import RRF_K, HybridRetriever, RetrievalUnavailable, reciprocal_rank_fusion
from tests.conftest import UnreachableCollection

COMPARISON = "qual a diferença entre o crediário e o get tap?"
CREDIARIO = "https://site.getnet.com.br/crediario/"
GET_TAP = "https://site.getnet.com.br/get-tap/"


def pages(result) -> set[str]:
    return {hit.chunk.url for hit in result.hits}


def test_rrf_prefers_the_document_both_retrievers_rank() -> None:
    """A chunk ranked second by both beats one ranked first by a single retriever."""
    scores = reciprocal_rank_fusion(bm25_order=["a", "b"], dense_order=["c", "b"])

    assert scores["b"] == 2 / (RRF_K + 2)
    assert scores["a"] == 1 / (RRF_K + 1)
    assert scores["b"] > scores["a"] > 0
    assert max(scores, key=scores.get) == "b"


def test_fusion_order_is_by_rank_not_by_raw_score() -> None:
    """Only positions enter the fusion: BM25 and cosine live on incomparable scales."""
    scores = reciprocal_rank_fusion(bm25_order=["x"], dense_order=["y"])

    assert scores["x"] == scores["y"]


def test_search_returns_top_k_with_scores_and_metadata(sample_chunks, fake_collection) -> None:
    """Every hit carries the evidence needed to cite it and to explain its rank."""
    retriever = HybridRetriever(sample_chunks, fake_collection(), top_k=5)

    result = retriever.search("em quantas parcelas posso dividir uma venda no crediário")

    assert 0 < len(result.hits) <= 5
    top = result.hits[0]
    assert top.chunk.url.startswith("https://")
    assert top.rrf_score > 0
    assert top.bm25_rank is not None or top.dense_rank is not None
    assert "parcela" in top.chunk.text.lower() or "crediário" in top.chunk.text.lower()


def test_confidence_is_absolute_similarity_not_fusion_rank(sample_chunks, fake_collection) -> None:
    """An off-topic query still ranks something; confidence is what makes it refusable."""
    weak = {chunk.id: 0.05 for chunk in sample_chunks}
    retriever = HybridRetriever(sample_chunks, fake_collection(weak), top_k=5)

    result = retriever.search("qual a cotação do euro hoje")

    assert result.hits, "fusion always returns an order"
    assert result.confidence == pytest.approx(0.05)


def test_a_comparison_retrieves_the_page_of_each_item(sample_chunks, fake_collection) -> None:
    """The question alone ranks generic pages first; one search per item brings each page in."""
    retriever = HybridRetriever(sample_chunks, fake_collection(), top_k=3)

    alone = retriever.search(COMPARISON)
    per_item = retriever.search(COMPARISON, ["crediário", "get tap"])

    assert GET_TAP not in pages(alone)
    assert {CREDIARIO, GET_TAP} <= pages(per_item)
    assert per_item.hits[0] == alone.hits[0], "the question's own best hit still comes first"


def test_an_unreachable_embedder_raises_its_own_error(sample_chunks) -> None:
    retriever = HybridRetriever(sample_chunks, UnreachableCollection(), top_k=5)

    with pytest.raises(RetrievalUnavailable):
        retriever.search(COMPARISON)


def test_confidence_is_the_best_match_any_query_found(sample_chunks, fake_collection) -> None:
    """A strong dense match outside the kept hits still says the corpus has the subject."""
    lexical = next(c for c in sample_chunks if "crediário" in c.text.lower())
    semantic = next(c for c in sample_chunks if "/get-tap/" in c.url)
    similarities = {chunk.id: 0.0 for chunk in sample_chunks}
    similarities.update({lexical.id: 0.3, semantic.id: 0.9})
    retriever = HybridRetriever(sample_chunks, fake_collection(similarities), top_k=1)

    result = retriever.search("parcelamento no crediário")

    assert [hit.chunk.id for hit in result.hits] == [lexical.id]
    assert result.confidence == pytest.approx(0.9)


def test_a_reranker_reorders_the_fused_candidates(sample_chunks, fake_collection) -> None:
    """The reranker decides the order and the cut; confidence stays the dense similarity."""
    fused = HybridRetriever(sample_chunks, fake_collection(), top_k=20)
    query = "em quantas parcelas posso dividir uma venda no crediário"
    last = fused.search(query).hits[-1].chunk

    def prefer_last(_query, texts):
        return [1.0 if text == last.text else 0.0 for text in texts]

    reranked = HybridRetriever(sample_chunks, fake_collection(), top_k=5, rerank=prefer_last)
    result = reranked.search(query)

    assert result.hits[0].chunk == last
    assert result.confidence == fused.search(query).confidence


def test_a_failing_reranker_keeps_the_fused_order(sample_chunks, fake_collection) -> None:
    """A better order is an improvement, never a reason to answer nothing."""

    def broken(_query, _texts):
        raise TimeoutError("reranker down")

    query = "em quantas parcelas posso dividir uma venda no crediário"
    fused = HybridRetriever(sample_chunks, fake_collection(), top_k=5).search(query)
    result = HybridRetriever(sample_chunks, fake_collection(), top_k=5, rerank=broken).search(query)

    assert [hit.chunk.id for hit in result.hits] == [hit.chunk.id for hit in fused.hits]
