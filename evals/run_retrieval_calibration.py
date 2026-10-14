"""Retrieval threshold calibration: in-domain against out-of-domain confidence, per embedder.

    uv run python -m evals.run_retrieval_calibration

Builds a throwaway index from the committed corpus snapshot for each candidate embedder, runs
every query of `evals/retrieval_calibration.jsonl` through the hybrid retriever, and writes
`evals/retrieval_calibration.md`: the score ranges, the gap between the two groups, the best
single threshold and what it would cost. The OpenAI embedders need OPENAI_API_KEY and cost a
fraction of a cent. Like the other evals, it never runs in pytest.
"""

import json
import statistics
import tempfile
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.rag.chunk import chunk_documents
from app.rag.ingest import chunks_path, index_chunks, load_documents, write_chunks
from app.rag.retrieve import HybridRetriever
from app.settings import Settings, get_settings

QUERIES = Path(__file__).parent / "retrieval_calibration.jsonl"
REPORT = Path(__file__).parent / "retrieval_calibration.md"

EMBEDDERS = [
    ("local", "chroma default (all-MiniLM-L6-v2)"),
    ("openai", "text-embedding-3-small"),
    ("openai", "text-embedding-3-large"),
]


@dataclass
class Scored:
    """One embedder's confidence for every query, and its per-query search time."""

    label: str
    inside: dict[str, float]
    outside: dict[str, float]
    seconds: list[float]

    def gap(self) -> float:
        """Positive when every in-domain query outscores every out-of-domain one."""
        return min(self.inside.values()) - max(self.outside.values())

    def auc(self) -> float:
        """Chance that a random in-domain query outscores a random out-of-domain one."""
        pairs = [(i, o) for i in self.inside.values() for o in self.outside.values()]
        return sum(1.0 if i > o else 0.5 if i == o else 0.0 for i, o in pairs) / len(pairs)

    def best_threshold(self) -> tuple[float, int, int]:
        """The cut that gets the most queries right, with in-domain kept and out-of-domain cut."""
        cuts = sorted({*self.inside.values(), *self.outside.values()})
        scored = []
        for cut in cuts:
            kept = sum(score >= cut for score in self.inside.values())
            rejected = sum(score < cut for score in self.outside.values())
            scored.append((kept + rejected, cut, kept, rejected))
        _, cut, kept, rejected = max(scored)
        return cut, kept, rejected


def embedder_settings(backend: str, model: str, index_path: str) -> Settings:
    """The configured settings, pointed at a scratch index built with one embedder."""
    return get_settings().model_copy(
        update={"embedding_backend": backend, "embedding_model": model, "index_path": index_path}
    )


def score(backend: str, model: str, rows: list[dict], chunks) -> Scored:
    """Index the corpus with one embedder and record each query's retrieval confidence."""
    with tempfile.TemporaryDirectory() as scratch:
        settings = embedder_settings(backend, model, str(Path(scratch) / "chroma"))
        write_chunks(chunks, chunks_path(settings))
        index_chunks(chunks, settings)
        retriever = HybridRetriever.from_settings(settings)
        inside, outside, seconds = {}, {}, []
        for row in rows:
            start = time.perf_counter()
            confidence = retriever.search(row["query"]).confidence
            seconds.append(time.perf_counter() - start)
            group = inside if row["group"] == "in_domain" else outside
            group[row["id"]] = confidence
    label = f"{backend}: `{model}`" if backend == "openai" else model
    return Scored(label, inside, outside, seconds)


def render(results: list[Scored], rows: list[dict]) -> str:
    """The committed report."""
    groups = ("in_domain", "out_of_domain")
    count = {group: sum(row["group"] == group for row in rows) for group in groups}
    lines = [
        "# Retrieval threshold calibration",
        "",
        f"{count['in_domain']} in-domain questions the corpus answers and {count['out_of_domain']} "
        f"out-of-domain ones, from `evals/{QUERIES.name}`, each run through the hybrid retriever "
        "over an index built from the committed snapshot with one embedder. Confidence is the best "
        "dense similarity among the fused hits. Written by "
        f"`uv run python -m evals.run_retrieval_calibration` on {date.today().isoformat()}.",
        "",
        "| Embedder | In-domain min / median | Out-of-domain median / max | Gap | AUC "
        "| Best threshold | Kept / rejected at it | Search p50 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        cut, kept, rejected = result.best_threshold()
        inside, outside = list(result.inside.values()), list(result.outside.values())
        lines.append(
            f"| {result.label} "
            f"| {min(inside):.3f} / {statistics.median(inside):.3f} "
            f"| {statistics.median(outside):.3f} / {max(outside):.3f} "
            f"| {result.gap():+.3f} | {result.auc():.3f} | {cut:.3f} "
            f"| {kept}/{len(result.inside)} kept, {rejected}/{len(result.outside)} rejected "
            f"| {statistics.median(result.seconds) * 1000:.0f} ms |"
        )
    lines += [
        "",
        "Gap is the lowest in-domain score minus the highest out-of-domain one: positive means "
        "one threshold separates the groups completely. AUC is the chance that a random "
        "in-domain question outscores a random out-of-domain one.",
        "",
        "## Per query",
        "",
        "| Query | Group | " + " | ".join(result.label for result in results) + " |",
        "| --- | --- | " + " | ".join("---" for _ in results) + " |",
    ]
    for row in rows:
        values = [
            (result.inside if row["group"] == "in_domain" else result.outside)[row["id"]]
            for result in results
        ]
        lines.append(
            f"| {row['id']} {row['query']} | {row['group']} | "
            + " | ".join(f"{value:.3f}" for value in values)
            + " |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    with QUERIES.open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    documents, _, _ = load_documents()
    chunks, _ = chunk_documents(documents)
    results = [score(backend, model, rows, chunks) for backend, model in EMBEDDERS]
    REPORT.write_text(render(results, rows), encoding="utf-8")
    print(REPORT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
