"""Reranker benchmark: recall@5, MRR and the latency each search adds, RRF alone against rerankers.

    uv run --group rerank python -m evals.run_rerank_benchmark

Builds a throwaway index from the committed snapshot with the configured embedder (a fraction of
a cent on the OpenAI key), runs every in-domain question of `evals/retrieval_calibration.jsonl`
through the hybrid retriever, and lets each reranker reorder the same 20 fused candidates: the
dense side is queried once per question and reused. Local models are fetched on the first run
and scored on the CPU; Cohere needs COHERE_API_KEY and is reported as not run without it. Writes
`evals/rerank_benchmark.md`, which says whether the gold was reviewed. Never runs in pytest.
"""

import platform
import statistics
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.providers.embeddings import build_embedding_function
from app.rag.chunk import chunk_documents
from app.rag.ingest import index_chunks, read_snapshot
from app.rag.rerank import (
    COHERE_TRIAL_INTERVAL_S,
    LOCAL_MODELS,
    Rerank,
    cohere_reranker,
    local_reranker,
)
from app.rag.retrieve import HybridRetriever
from app.settings import Settings, get_settings
from evals.retrieval_review import QUERIES, is_reviewed, read_set

REPORT = Path(__file__).parent / "rerank_benchmark.md"
CANDIDATES = 20
LATENCY_BUDGET_S = 1.0
"""The p95 a reranker may add per search, set in specs/tasks.md (T-53) before measuring: an
eighth of the 8 s at which the node latency alert fires."""


@dataclass
class Run:
    """One reranker's rank of the first gold chunk per question, its time per search, and the
    searches where it failed and the fused order stood, as it does in the service."""

    label: str
    ranks: dict[str, int | None] = field(default_factory=dict)
    seconds: list[float] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    error: str | None = None

    def recall(self, k: int = 5) -> float:
        """Share of questions with a gold chunk in the top k."""
        return sum(rank is not None and rank <= k for rank in self.ranks.values()) / len(self.ranks)

    def mrr(self) -> float:
        """Mean reciprocal rank of the first gold chunk, 0 when none is in the candidates."""
        return statistics.mean(1 / rank if rank else 0.0 for rank in self.ranks.values())

    def quantile(self, q: float) -> float:
        """Rerank seconds at quantile q."""
        ordered = sorted(self.seconds)
        return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


class CachedCollection:
    """The dense side queried once per query text, so every run reranks the same candidates."""

    def __init__(self, collection) -> None:
        self._collection = collection
        self._answers: dict[tuple[str, int], dict] = {}

    def query(self, query_texts: list[str], n_results: int) -> dict:
        key = (query_texts[0], n_results)
        if key not in self._answers:
            self._answers[key] = self._collection.query(
                query_texts=query_texts, n_results=n_results
            )
        return self._answers[key]


def timed(rerank: Rerank, run: Run) -> Rerank:
    """The reranker, recording its time per call and each error it raises."""

    def call(query, texts):
        start = time.perf_counter()
        try:
            return rerank(query, texts)
        except Exception as exc:
            run.failures.append(type(exc).__name__)
            raise
        finally:
            run.seconds.append(time.perf_counter() - start)

    return call


def measure(
    run: Run, rerank: Rerank | None, rows: list[dict], chunks, collection, interval: float = 0.0
) -> Run:
    """Search every question and record where its first gold chunk ranks, waiting `interval`
    seconds before each search, outside the measurement."""
    if rerank is not None:
        rerank("warm-up", ["the first call loads the model and is not measured"])
    reranker = timed(rerank, run) if rerank else None
    retriever = HybridRetriever(chunks, collection, top_k=CANDIDATES, rerank=reranker)
    for row in rows:
        time.sleep(interval)
        ids = [hit.chunk.id for hit in retriever.search(row["query"]).hits]
        run.ranks[row["id"]] = next(
            (rank for rank, chunk_id in enumerate(ids, start=1) if chunk_id in row["gold"]), None
        )
    return run


def rerankers(
    settings: Settings,
) -> list[tuple[str, Callable[[], Rerank | None] | None, float]]:
    """Each candidate, how to build it (None for Cohere without a key) and the pause between
    its searches."""
    cohere = (
        (lambda: cohere_reranker(settings.cohere_api_key, settings.rerank_timeout_s))
        if settings.cohere_api_key
        else None
    )
    return [
        ("none", lambda: None, 0.0),
        ("bge", lambda: local_reranker(LOCAL_MODELS["bge"], download=True), 0.0),
        ("qwen", lambda: local_reranker(LOCAL_MODELS["qwen"], download=True), 0.0),
        ("cohere", cohere, COHERE_TRIAL_INTERVAL_S),
    ]


def rule_pick(runs: list[Run]) -> str:
    """The rule from T-53: the reranker with the best MRR among those that raise recall@5 or
    MRR over RRF alone within the latency budget, else none."""
    baseline = runs[0]
    winners = [
        run
        for run in runs[1:]
        if run.ranks
        and run.quantile(0.95) <= LATENCY_BUDGET_S
        and (run.recall() > baseline.recall() or run.mrr() > baseline.mrr())
    ]
    return max(winners, key=Run.mrr).label if winners else "none"


def render(runs: list[Run], rows: list[dict], chunks: int, settings: Settings) -> str:
    """The committed report."""
    reviewed = (
        "reviewed by the owner through `evals/retrieval_review.csv`"
        if is_reviewed(read_set())
        else "**not reviewed yet**: proposed, pending the owner's review"
    )
    lines = [
        "# Reranker benchmark",
        "",
        f"{len(rows)} in-domain questions from `evals/{QUERIES.name}`, with "
        f"{sum(len(row['gold']) for row in rows)} gold chunks ({reviewed}). Each ran through the "
        f"hybrid retriever over an index of the committed snapshot ({chunks} chunks, "
        f"`{settings.embedding_model}`), and each reranker reordered the same {CANDIDATES} fused "
        f"candidates. Written by `uv run --group rerank python -m evals.run_rerank_benchmark` on "
        f"{date.today().isoformat()}, {platform.system()} {platform.machine()}, local models on "
        "the CPU.",
        "",
        "| Reranker | recall@5 | MRR | Rerank p50 | Rerank p95 | Within the 1 s p95 budget |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines += [summary_row(run, len(rows)) for run in runs]
    lines += [
        "",
        "recall@5 counts a question when any of its gold chunks is in the top 5. MRR is the mean "
        f"of 1/rank of the first gold chunk among the {CANDIDATES} candidates, 0 when none is "
        "there. Rerank time is the reranker's own, per search, after a first call that loads it. A "
        "search where the reranker failed keeps the fused order, as in the service, and its time "
        "still counts.",
        "",
        f"**Rule:** the rule in T-53 picks `{rule_pick(runs)}`: on only if it raises recall@5 or "
        "MRR within the latency budget. The default is decided in "
        "`docs/adr/0002-reranker-off-by-default.md`.",
        "",
        *per_question(runs, rows),
    ]
    return "\n".join(lines) + "\n"


def summary_row(run: Run, questions: int) -> str:
    """One reranker's line of the summary table."""
    if run.error:
        return f"| {run.label} | not run: {run.error} | | | | |"
    hits = sum(rank is not None and rank <= 5 for rank in run.ranks.values())
    timing = "— | — | —"
    if run.seconds:
        p50, p95 = run.quantile(0.5), run.quantile(0.95)
        verdict = "yes" if p95 <= LATENCY_BUDGET_S else "no"
        timing = f"{p50 * 1000:.0f} ms | {p95 * 1000:.0f} ms | {verdict}"
    failed = (
        f" ({len(run.failures)} failed: {', '.join(sorted(set(run.failures)))})"
        if run.failures
        else ""
    )
    return (
        f"| {run.label}{failed} | {run.recall():.3f} ({hits}/{questions}) | {run.mrr():.3f} "
        f"| {timing} |"
    )


def per_question(runs: list[Run], rows: list[dict]) -> list[str]:
    """Where each question's first gold chunk ranked, per reranker."""
    lines = [
        "## Rank of the first gold chunk",
        "",
        "| Question | Origin | " + " | ".join(run.label for run in runs) + " |",
        "| --- | --- | " + " | ".join("---" for _ in runs) + " |",
    ]
    for row in rows:
        cells = " | ".join(str(run.ranks.get(row["id"]) or "—") for run in runs)
        lines.append(f"| {row['id']} {row['query']} | {row.get('origin', 'set')} | {cells} |")
    return lines


def scratch_collection(settings: Settings, chunks):
    """A collection of the snapshot's chunks, indexed with the configured embedder."""
    import chromadb

    index_chunks(chunks, settings)
    client = chromadb.PersistentClient(path=settings.index_path)
    return client.get_collection(
        name=settings.collection_name, embedding_function=build_embedding_function(settings)
    )


def main() -> None:
    rows = [row for row in read_set() if row["group"] == "in_domain"]
    chunks, _ = chunk_documents(read_snapshot())
    with tempfile.TemporaryDirectory() as scratch:
        settings = get_settings().model_copy(update={"index_path": str(Path(scratch) / "chroma")})
        collection = CachedCollection(scratch_collection(settings, chunks))
        runs = []
        for label, build, interval in rerankers(settings):
            if build is None:
                runs.append(Run(label, error="no COHERE_API_KEY"))
                continue
            runs.append(measure(Run(label), build(), rows, chunks, collection, interval))
            print(f"{label}: recall@5 {runs[-1].recall():.3f}, MRR {runs[-1].mrr():.3f}")
    REPORT.write_text(render(runs, rows, len(chunks), settings), encoding="utf-8")
    print(REPORT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
