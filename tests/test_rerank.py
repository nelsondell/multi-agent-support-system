"""The reranker's configuration paths and the review of the retrieval set's gold."""

import httpx
import pytest

from app.rag import rerank
from app.settings import Settings
from evals.retrieval_review import apply_review, problems


def test_without_a_reranker_the_fused_order_is_kept() -> None:
    assert rerank.build_reranker(Settings(reranker="none")) is None


def test_cohere_without_a_key_keeps_the_fused_order() -> None:
    assert rerank.build_reranker(Settings(reranker="cohere", cohere_api_key=None)) is None


def test_local_weights_never_download_at_query_time(monkeypatch) -> None:
    """The service only loads weights fetched beforehand; missing ones leave the fused order."""
    asked = []

    def missing(model, download):
        asked.append(download)
        raise OSError(f"{model} is not in the local cache")

    monkeypatch.setattr(rerank, "local_reranker", missing)

    assert rerank.build_reranker(Settings(reranker="bge")) is None
    assert asked == [False]


def test_cohere_gets_a_masked_query_and_scores_come_back_in_order(monkeypatch) -> None:
    sent = {}

    def post(url, headers, json, timeout):
        sent.update(json)
        results = [{"index": 2, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.4}]
        return httpx.Response(200, json={"results": results}, request=httpx.Request("POST", url))

    monkeypatch.setattr(rerank.httpx, "post", post)
    scores = rerank.cohere_reranker("key", 1.0)("meu CPF é 123.456.789-09", ["a", "b", "c"])

    assert scores == [0.4, 0.0, 0.9]
    assert sent["model"] == rerank.COHERE_MODEL and sent["documents"] == ["a", "b", "c"]
    assert "123.456.789-09" not in sent["query"]


SET = [
    {"id": "N01", "group": "in_domain", "query": "q1", "gold": ["c1", "c2"]},
    {"id": "O01", "group": "out_of_domain", "query": "q2"},
]
CHUNKS = {"c1": object(), "c2": object(), "c3": object()}


def line(qid: str, chunk_id: str, keep: str) -> dict:
    return {"id": qid, "chunk_id": chunk_id, "keep": keep}


def test_a_review_with_a_blank_verdict_is_not_imported() -> None:
    review = [line("N01", "c1", "yes"), line("N01", "c2", "")]

    assert problems(review, SET, CHUNKS) == ["N01 c2: keep must be yes or no"]


@pytest.mark.parametrize("keep", ["no", "No "])
def test_the_gold_becomes_the_chunks_kept_and_the_ones_added(keep) -> None:
    review = [line("N01", "c1", "yes"), line("N01", "c2", keep), line("N01", "c3", "yes")]

    assert problems(review, SET, CHUNKS) == []
    assert apply_review(review, SET)[0]["gold"] == ["c1", "c3"]
    assert apply_review(review, SET)[1] == SET[1]
