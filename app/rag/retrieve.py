"""Hybrid retrieval: BM25 and dense vectors, fused with Reciprocal Rank Fusion.

Two retrievers because they fail differently. Dense embeddings carry an English question into a
Portuguese corpus, which is the normal case here. BM25 pins exact product names — "Get Smart"
against "Get Mini" — where embeddings blur near-identical marketing copy.

The query used is `query_pt` from the router: the corpus is Brazilian Portuguese, so retrieval
always runs in Portuguese even when the merchant wrote in English.

An optional reranker (`app/rag/rerank.py`) then reorders each query's fused candidates.

A comparison also carries one short search per item compared. Each query's two rankings are fused
with RRF on their own, and the per-query rankings are then interleaved in rounds: in each round,
every query adds its best hit not yet taken. Every query gets the same number of rounds, so a
comparison may return a few more than top-k hits rather than cut the last item's search short.
Fusing every list at once was measured and rejected: RRF rewards consensus, so generic pages that
rank fairly well for every query crowd out the one page each item is actually about.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

from app.providers.embeddings import build_embedding_function
from app.rag.chunk import Chunk, tokenize
from app.rag.rerank import Rerank, build_reranker
from app.settings import Settings

logger = logging.getLogger(__name__)

RRF_K = 60
"""Rank-fusion damping constant. 60 is the value from the original RRF paper: large enough that
the top few ranks score close together, so a document both retrievers like beats one that only a
single retriever ranks first."""

_CANDIDATES = 20


@dataclass(frozen=True)
class Hit:
    """A retrieved chunk with the evidence for why it ranked where it did."""

    chunk: Chunk
    rrf_score: float
    dense_similarity: float
    bm25_rank: int | None
    dense_rank: int | None


@dataclass(frozen=True)
class RetrievalResult:
    """What the knowledge agent receives."""

    hits: list[Hit]
    confidence: float
    """Best dense similarity any of the queries reached.

    RRF is a ranking signal, not a relevance one: a document that tops both lists scores the same
    whether the corpus answers the question or not. Abstention therefore keys off an absolute
    similarity, while RRF only decides the order.
    """


def load_chunks(path: Path) -> list[Chunk]:
    """Read the chunk corpus written by ingestion."""
    if not path.is_file():
        raise FileNotFoundError(
            f"chunk corpus not found at {path}; build it with `uv run python -m app.rag.ingest`"
        )
    with path.open(encoding="utf-8") as handle:
        return [Chunk(**json.loads(line)) for line in handle if line.strip()]


class RetrievalUnavailable(RuntimeError):
    """Raised when the dense side cannot be queried, e.g. the embedding API is down."""


def reciprocal_rank_fusion(
    bm25_order: list[str], dense_order: list[str], k: int = RRF_K
) -> dict[str, float]:
    """Fuse two ranked id lists into one score per id.

    Each list contributes 1 / (k + rank), with rank starting at 1. Written out rather than
    imported so the ranking is auditable: only positions matter, never the raw BM25 or cosine
    values, which live on scales that cannot be compared.
    """
    scores: dict[str, float] = {}
    for order in (bm25_order, dense_order):
        for position, chunk_id in enumerate(order, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + position)
    return scores


class HybridRetriever:
    """Holds the BM25 index in memory and queries Chroma for the dense side."""

    def __init__(
        self, chunks: list[Chunk], collection, top_k: int = 5, rerank: Rerank | None = None
    ) -> None:
        self._chunks = {chunk.id: chunk for chunk in chunks}
        self._ids = [chunk.id for chunk in chunks]
        self._bm25 = BM25Okapi([tokenize(chunk.text) for chunk in chunks])
        self._collection = collection
        self._top_k = top_k
        self._rerank = rerank

    @property
    def size(self) -> int:
        """How many chunks the index holds; zero means it is not ready to answer."""
        return len(self._ids)

    @classmethod
    def from_settings(cls, settings: Settings) -> "HybridRetriever":
        """Open the persisted index built by ingestion."""
        import chromadb

        chunks = load_chunks(Path(settings.index_path).parent / "chunks.jsonl")
        client = chromadb.PersistentClient(path=settings.index_path)
        try:
            collection = client.get_collection(
                name=settings.collection_name,
                embedding_function=build_embedding_function(settings),
            )
        except ValueError as exc:
            # The stored vectors belong to whichever embedder built them; querying with another
            # would compare incomparable spaces. Chroma refuses, and so do we, with the fix.
            raise ValueError(
                f"index at {settings.index_path} was built with a different embedder "
                f"({exc}); rebuild it with `uv run python -m app.rag.ingest` or set "
                "EMBEDDING_BACKEND to the one it was built with"
            ) from exc
        return cls(chunks, collection, settings.retrieval_top_k, build_reranker(settings))

    def _bm25_order(self, query: str) -> list[str]:
        """Chunk ids ranked by lexical score."""
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(zip(self._ids, scores, strict=True), key=lambda pair: -pair[1])
        return [chunk_id for chunk_id, score in ranked[:_CANDIDATES] if score > 0]

    def _dense_order(self, query: str) -> tuple[list[str], dict[str, float]]:
        """Chunk ids ranked by vector similarity, with the similarities themselves."""
        try:
            response = self._collection.query(query_texts=[query], n_results=_CANDIDATES)
        except Exception as exc:  # the embedder may be a remote API with its own error types
            raise RetrievalUnavailable(f"dense retrieval failed: {exc}") from exc
        ids = response["ids"][0]
        distances = response["distances"][0]
        return ids, {i: 1.0 - d for i, d in zip(ids, distances, strict=True)}

    def _ranking(self, query: str) -> list[Hit]:
        """One query's chunks, its BM25 and dense rankings fused with RRF, best first."""
        bm25_order = self._bm25_order(query)
        dense_order, similarities = self._dense_order(query)
        fused = reciprocal_rank_fusion(bm25_order, dense_order)
        hits = [
            Hit(
                chunk=self._chunks[chunk_id],
                rrf_score=score,
                dense_similarity=similarities.get(chunk_id, 0.0),
                bm25_rank=bm25_order.index(chunk_id) + 1 if chunk_id in bm25_order else None,
                dense_rank=dense_order.index(chunk_id) + 1 if chunk_id in dense_order else None,
            )
            for chunk_id, score in sorted(fused.items(), key=lambda pair: -pair[1])
            if chunk_id in self._chunks
        ]
        return self._reranked(query, hits)

    def _reranked(self, query: str, hits: list[Hit]) -> list[Hit]:
        """The first candidates reordered by the reranker, or as fused when there is none or
        it fails: a better order is an improvement, never a reason to answer nothing."""
        if self._rerank is None:
            return hits
        head = hits[:_CANDIDATES]
        try:
            scores = self._rerank(query, [hit.chunk.text for hit in head])
        except Exception as exc:  # a local model or a remote API, each with its own errors
            logger.warning("rerank failed (%s); keeping the fused order", type(exc).__name__)
            return hits
        order = sorted(range(len(head)), key=lambda index: -scores[index])
        return [head[index] for index in order] + hits[_CANDIDATES:]

    def search(self, query_pt: str, also: Sequence[str] = ()) -> RetrievalResult:
        """Retrieve for the whole question and any per-item searches: top-k hits, or a few more
        for a comparison, so that every query gets the same number of rounds.

        The question is always one of the queries; confidence is the best dense similarity any
        of them reached.

        Raises `RetrievalUnavailable` when the dense side cannot be queried.
        """
        rankings = [self._ranking(query) for query in dict.fromkeys([query_pt, *also])]
        hits = interleave(rankings, rounds=-(-self._top_k // len(rankings)))
        # The best match any query found, not only among the hits kept: a per-item search can
        # be the one that finds the page, and the question as a whole must not veto it.
        confidence = max(
            (hit.dense_similarity for ranking in rankings for hit in ranking), default=0.0
        )
        return RetrievalResult(hits=hits, confidence=confidence)


def interleave(rankings: list[list[Hit]], rounds: int) -> list[Hit]:
    """`rounds` rounds in which every ranking, in turn, adds its best hit not yet taken.

    A hit another query already took does not cost a ranking its turn, so each per-item search
    keeps its own top results. With a single ranking this is that ranking's first `rounds` hits.
    """
    merged: list[Hit] = []
    taken: set[str] = set()
    remaining = [iter(ranking) for ranking in rankings]
    for _ in range(rounds):
        for hits in remaining:
            hit = next((hit for hit in hits if hit.chunk.id not in taken), None)
            if hit is not None:
                taken.add(hit.chunk.id)
                merged.append(hit)
    return merged
