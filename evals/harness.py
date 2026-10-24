"""The real API for eval runs, live or replayed from a recording.

Live, the model, retrieval, web search and judge answer for real, and every answer is recorded,
keyed by what was asked. Replayed, the same requests are answered from the recording, with no key
and no network. A question the recording lacks fails the run: a prompt or the code changed what is
asked, so the recording has to be made again live. Volatile ids (protocols, proposal ids, ticket
ids, timestamps) are masked out of the keys.

A case may plant a page in the web results or a passage in the corpus results, both live and
replayed, to test indirect injection without touching the real corpus or the internet.
"""

import hashlib
import json
import logging
import re
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import httpx

import app.main
from app.auth import RateLimiter, TokenVerifier
from app.graph.build import build_graph
from app.main import create_app
from app.observability import configure_logging
from app.providers.llm import LLMProviderError, build_provider
from app.rag.chunk import Chunk, chunk_documents
from app.rag.ingest import read_snapshot
from app.rag.rerank import COHERE_TRIAL_INTERVAL_S
from app.rag.retrieve import Hit, HybridRetriever, RetrievalResult, RetrievalUnavailable
from app.settings import Settings, get_settings
from app.storage import open_storage
from app.tools.web import NoWebResults, WebResult, WebSearch, WebSearchUnavailable
from evals.judges import judge_for
from scripts.dev_auth import PUBLIC, keys

_VOLATILE = re.compile(
    r"ESC-[0-9A-F]{10}|TKT-[0-9A-F]{8}|\b[0-9a-f]{32}\b|\d{4}-\d{2}-\d{2}T[0-9:.]+[+-Z0-9:]*"
)
_ERRORS = {error.__name__: error for error in (LLMProviderError, RetrievalUnavailable)}
_ERRORS |= {error.__name__: error for error in (NoWebResults, WebSearchUnavailable)}

PLANTED: ContextVar[dict | None] = ContextVar("planted", default=None)
"""Per case: `web`, pages added to every web result, and `corpus`, a passage added to retrieval."""


class NotRecorded(RuntimeError):
    """A replay asked something the recording never saw: record again with `make eval`."""


class Recording:
    def __init__(self, path: Path, live: bool, reuse: bool = False) -> None:
        self.path, self.live, self.reuse, self.calls, self.used = path, live, reuse, {}, set()
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                call = json.loads(line)
                self.calls[call["key"]] = call["value"]

    def key(self, kind: str, *asked: Any) -> str:
        text = _VOLATILE.sub("<id>", json.dumps(asked, ensure_ascii=False, sort_keys=True))
        return f"{kind}:{hashlib.sha256(text.encode()).hexdigest()[:24]}"

    def replayed(self, key: str, decode):
        self.used.add(key)
        if key not in self.calls:
            raise NotRecorded(f"{key} is not in {self.path.name}; record it again with `make eval`")
        value = self.calls[key]
        if isinstance(value, dict) and set(value) == {"error", "message"}:
            raise _ERRORS[value["error"]](value["message"])
        return decode(value)

    def failed(self, key: str, exc: Exception) -> None:
        self.calls[key] = {"error": type(exc).__name__, "message": str(exc)}

    async def answer(self, key: str, call, encode, decode):
        """Live, run `call` and record what it returned or raised; replayed, give that back."""
        if not self.live or (self.reuse and key in self.calls):
            return self.replayed(key, decode)
        self.used.add(key)
        try:
            value = await call()
        except tuple(_ERRORS.values()) as exc:
            self.failed(key, exc)
            raise
        self.calls[key] = encode(value)
        return value

    def save(self) -> None:
        if self.reuse:  # an updated recording keeps only what this run asked
            self.calls = {
                k: v for k, v in self.calls.items() if k in self.used or k.startswith("meta:")
            }
        lines = [
            json.dumps({"key": k, "value": v}, ensure_ascii=False)
            for k, v in sorted(self.calls.items())
        ]
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class RecordedModel:
    """The chat model or the judge."""

    def __init__(self, inner, recording: Recording, kind: str) -> None:
        self._inner, self._recording, self._kind = inner, recording, kind

    async def structured(self, *, system: str, user: str, schema: type):
        key = self._recording.key(self._kind, schema.__name__, system, user)

        async def call():
            return await self._inner.structured(system=system, user=user, schema=schema)

        dump = lambda value: value.model_dump(mode="json")  # noqa: E731
        return await self._recording.answer(key, call, dump, schema.model_validate)


class RecordedRetriever:
    def __init__(
        self,
        inner: HybridRetriever | None,
        recording: Recording,
        chunks: list[Chunk],
        pace_s: float = 0.0,
    ):
        self._inner, self._recording = inner, recording
        self._chunks = {chunk.id: chunk for chunk in chunks}
        self.size = len(chunks)
        self._pace_s, self._next = pace_s, 0.0

    def _wait(self, queries: int) -> None:
        """Space live searches so each of their rerank calls stays under a trial key's limit."""
        time.sleep(max(0.0, self._next - time.monotonic()))
        self._next = time.monotonic() + self._pace_s * queries

    def search(self, query_pt: str, also=()) -> RetrievalResult:
        key = self._recording.key("retrieval", query_pt, list(also))
        recording = self._recording
        if recording.live and not (recording.reuse and key in recording.calls):
            recording.used.add(key)
            self._wait(len(dict.fromkeys([query_pt, *also])))
            try:
                result = self._inner.search(query_pt, also)
            except RetrievalUnavailable as exc:
                self._recording.failed(key, exc)
                raise
            self._recording.calls[key] = self._encode(result)
        else:
            result = self._recording.replayed(key, self._decode)
        planted = (PLANTED.get() or {}).get("corpus")
        if not planted:
            return result
        chunk = Chunk(id="planted", section="", route="faq", **planted)
        hit = Hit(chunk=chunk, rrf_score=1.0, dense_similarity=0.95, bm25_rank=1, dense_rank=1)
        return RetrievalResult(hits=[hit, *result.hits], confidence=max(result.confidence, 0.95))

    @staticmethod
    def _encode(result: RetrievalResult) -> dict:
        hits = [{**asdict(hit), "chunk": hit.chunk.id} for hit in result.hits]
        return {"confidence": result.confidence, "hits": hits}

    def _decode(self, value: dict) -> RetrievalResult:
        hits = [Hit(**{**hit, "chunk": self._chunks[hit["chunk"]]}) for hit in value["hits"]]
        return RetrievalResult(hits=hits, confidence=value["confidence"])


class RecordedWeb:
    def __init__(self, inner: WebSearch | None, recording: Recording) -> None:
        self._inner, self._recording = inner, recording

    async def search(self, query: str, recent: bool = False) -> list[WebResult]:
        key = self._recording.key("web", query, recent)
        encode = lambda results: [asdict(result) for result in results]  # noqa: E731
        decode = lambda rows: [WebResult(**row) for row in rows]  # noqa: E731
        results = await self._recording.answer(
            key, lambda: self._inner.search(query, recent), encode, decode
        )
        return [*(WebResult(**page) for page in (PLANTED.get() or {}).get("web", [])), *results]


@asynccontextmanager
async def open_api(
    recording: Recording, today: date
) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    """The app as its lifespan would build it, over a throwaway store, on the recording's day."""
    configure_logging(logging.WARNING)
    keys()
    live = recording.live
    settings: Settings = get_settings().model_copy(
        update={"state_dir": tempfile.mkdtemp(prefix="eval-"), "auth_jwks_url": str(PUBLIC)}
    )
    chunks, _ = chunk_documents(read_snapshot())
    pace_s = COHERE_TRIAL_INTERVAL_S if live and settings.reranker == "cohere" else 0.0
    retriever = RecordedRetriever(
        HybridRetriever.from_settings(settings) if live else None, recording, chunks, pace_s
    )
    model = RecordedModel(build_provider(settings) if live else None, recording, "model")
    real_today = app.main.merchant_today
    app.main.merchant_today = lambda: today
    try:
        async with open_storage(settings) as storage, httpx.AsyncClient() as client:
            web = RecordedWeb(WebSearch(settings, client) if live else None, recording)
            api = create_app()
            api.state.settings, api.state.retriever, api.state.audit = (
                settings,
                retriever,
                storage.audit,
            )
            api.state.queue, api.state.locks, api.state.desk = (
                storage.queue,
                storage.locks,
                storage.desk,
            )
            api.state.verifier = TokenVerifier.from_settings(settings)
            api.state.limiter = RateLimiter(10_000)
            api.state.graph = build_graph(
                model, settings, retriever, storage.checkpointer, storage.queue, web, storage.desk
            )
            transport = httpx.ASGITransport(app=api)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://eval", timeout=300
            ) as http:
                yield api, http
    finally:
        app.main.merchant_today = real_today


def recorded_judge(recording: Recording) -> RecordedModel:
    return RecordedModel(judge_for(get_settings()) if recording.live else None, recording, "judge")
