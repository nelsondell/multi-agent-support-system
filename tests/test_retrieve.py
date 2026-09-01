"""Hybrid retrieval and rank fusion, offline against the sampled knowledge base."""

import pytest

from app.rag.retrieve import RRF_K, HybridRetriever, reciprocal_rank_fusion


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
