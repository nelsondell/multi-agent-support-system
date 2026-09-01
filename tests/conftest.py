"""Shared fixtures. The suite runs offline: no vendor call, no network, no cost."""

import json
from pathlib import Path

import pytest

from app.rag.chunk import Chunk

FIXTURE = Path(__file__).parent / "fixtures" / "kb_sample.jsonl"


@pytest.fixture
def sample_chunks() -> list[Chunk]:
    """The sampled knowledge base, never the full corpus."""
    with FIXTURE.open(encoding="utf-8") as handle:
        return [Chunk(**json.loads(line)) for line in handle if line.strip()]


class FakeCollection:
    """Stands in for Chroma: ranks by token overlap so the tests need no embedding model."""

    def __init__(self, chunks: list[Chunk], similarities: dict[str, float] | None = None) -> None:
        self._chunks = chunks
        self._similarities = similarities or {}

    def query(self, query_texts: list[str], n_results: int) -> dict:
        from app.rag.chunk import tokenize

        terms = set(tokenize(query_texts[0]))
        scored = sorted(
            ((chunk.id, self._score(chunk, terms)) for chunk in self._chunks),
            key=lambda pair: -pair[1],
        )[:n_results]
        return {
            "ids": [[chunk_id for chunk_id, _ in scored]],
            "distances": [[1.0 - score for _, score in scored]],
        }

    def _score(self, chunk: Chunk, terms: set[str]) -> float:
        from app.rag.chunk import tokenize

        if chunk.id in self._similarities:
            return self._similarities[chunk.id]
        chunk_terms = set(tokenize(chunk.text))
        if not terms or not chunk_terms:
            return 0.0
        return len(terms & chunk_terms) / len(terms)


@pytest.fixture
def fake_collection(sample_chunks):
    """Factory for a dense side with controllable similarities."""

    def build(similarities: dict[str, float] | None = None) -> FakeCollection:
        return FakeCollection(sample_chunks, similarities)

    return build
