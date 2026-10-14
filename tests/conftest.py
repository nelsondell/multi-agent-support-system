"""Shared fixtures. The suite runs offline: no vendor call, no network, no cost."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.rag.chunk import Chunk

FIXTURE = Path(__file__).parent / "fixtures" / "kb_sample.jsonl"


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """The suite never reads the developer's configuration: no `.env`, and no setting exported in
    the shell. Every test sees the defaults plus whatever it passes itself."""
    from app.settings import Settings, get_settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def no_tracing():
    """No test uploads a trace, whatever the developer's environment says."""
    import langsmith

    langsmith.configure(enabled=False)


@pytest.fixture(autouse=True)
def pinned_today(monkeypatch):
    """The suite never reads the wall clock: `/chat` turns happen on the day the mock account
    records describe, so their dates are read as written."""
    from app.tools.account import RECORDED_ON

    monkeypatch.setattr("app.main.merchant_today", lambda: RECORDED_ON)


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


class UnreachableCollection:
    """A dense side whose embedder cannot be reached, as when its API is down."""

    def query(self, query_texts: list[str], n_results: int) -> dict:
        raise ConnectionError("embedding api unreachable")


@pytest.fixture
def fake_collection(sample_chunks):
    """Factory for a dense side with controllable similarities."""

    def build(similarities: dict[str, float] | None = None) -> FakeCollection:
        return FakeCollection(sample_chunks, similarities)

    return build


class ScriptedProvider:
    """A fake LLM: answers each schema with a preset object, or raises."""

    def __init__(self, responses: dict | None = None, error: str | None = None) -> None:
        self._responses = responses or {}
        self._error = error
        self.calls: list[tuple[str, str]] = []

    async def structured(self, *, system: str, user: str, schema: type):
        self.calls.append((schema.__name__, user))
        if self._error is not None:
            from app.providers.llm import LLMProviderError

            raise LLMProviderError(self._error)
        if schema not in self._responses:
            raise AssertionError(f"no scripted response for {schema.__name__}")
        return self._responses[schema]


@pytest.fixture
def build_test_graph(sample_chunks, fake_collection):
    """Compile the real graph over the sampled corpus and a scripted model."""

    def build(
        responses: dict | None = None,
        similarities: dict | None = None,
        checkpointer=None,
        web=None,
        **overrides,
    ):
        from app.graph.build import build_graph
        from app.rag.retrieve import HybridRetriever
        from app.settings import Settings

        settings = Settings(**overrides)
        retriever = HybridRetriever(sample_chunks, fake_collection(similarities), top_k=5)
        return build_graph(ScriptedProvider(responses), settings, retriever, checkpointer, web=web)

    return build


@pytest.fixture
def web_search():
    """A web search client over a mocked transport. Nothing leaves the process.

    Returns the client and the list of request bodies it sent. `respond` turns a request into
    the provider's response, or raises, to simulate a timeout.
    """
    from app.settings import Settings
    from app.tools.web import WebSearch

    def build(
        pages: list[dict] | None = None,
        key: str | None = "tvly-test",
        respond: Callable[[httpx.Request], httpx.Response] | None = None,
    ):
        sent: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(json.loads(request.content))
            if respond is not None:
                return respond(request)
            return httpx.Response(200, json={"results": pages or []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return WebSearch(Settings(tavily_api_key=key), client), sent

    return build
