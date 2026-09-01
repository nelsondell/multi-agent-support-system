"""Hybrid retrieval: BM25 and dense vectors, fused with Reciprocal Rank Fusion.

Two retrievers because they fail differently. Dense embeddings carry an English question into a
Portuguese corpus, which is the normal case here. BM25 pins exact product names — "Get Smart"
against "Get Mini" — where embeddings blur near-identical marketing copy.

The query used is `query_pt` from the router: the corpus is Brazilian Portuguese, so retrieval
always runs in Portuguese even when the merchant wrote in English.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

from app.providers.embeddings import build_embedding_function
from app.rag.chunk import Chunk, tokenize
from app.settings import Settings

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
    """Best dense similarity among the fused hits.

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

    def __init__(self, chunks: list[Chunk], collection, top_k: int = 5) -> None:
        self._chunks = {chunk.id: chunk for chunk in chunks}
        self._ids = [chunk.id for chunk in chunks]
        self._bm25 = BM25Okapi([tokenize(chunk.text) for chunk in chunks])
        self._collection = collection
        self._top_k = top_k

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
        return cls(chunks, collection, settings.retrieval_top_k)

    def _bm25_order(self, query: str) -> list[str]:
        """Chunk ids ranked by lexical score."""
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(zip(self._ids, scores, strict=True), key=lambda pair: -pair[1])
        return [chunk_id for chunk_id, score in ranked[:_CANDIDATES] if score > 0]

    def _dense_order(self, query: str) -> tuple[list[str], dict[str, float]]:
        """Chunk ids ranked by vector similarity, with the similarities themselves."""
        response = self._collection.query(query_texts=[query], n_results=_CANDIDATES)
        ids = response["ids"][0]
        distances = response["distances"][0]
        return ids, {i: 1.0 - d for i, d in zip(ids, distances, strict=True)}

    def search(self, query_pt: str) -> RetrievalResult:
        """Retrieve, fuse and cut to the configured top-k."""
        bm25_order = self._bm25_order(query_pt)
        dense_order, similarities = self._dense_order(query_pt)
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
        ][: self._top_k]

        confidence = max((hit.dense_similarity for hit in hits), default=0.0)
        return RetrievalResult(hits=hits, confidence=confidence)
