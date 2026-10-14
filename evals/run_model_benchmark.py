"""Router model benchmark: the routing golden set through each candidate chat model.

    uv run python -m evals.run_model_benchmark

Every candidate is a real model call per row and run, so this needs OPENAI_API_KEY and costs
cents. It writes `evals/router_benchmark.md`: accuracy, latency, tokens and cost per candidate,
and the rows each one missed. Like the routing eval, it is a measurement and never runs in pytest.
"""

import asyncio
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from langchain_core.callbacks import get_usage_metadata_callback

from app.graph.router import route
from app.prompts import load_prompt
from app.providers.llm import OpenAIProvider
from app.settings import get_settings
from evals.run_routing_eval import GOLDEN_SET, load_rows, routed_as, row_state

REPORT = Path(__file__).parent / "router_benchmark.md"
RUNS = 2
CONCURRENCY = 5

PRICES = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-6-luna": (0.10, 0.50),
    "gpt-6-sol": (2.00, 10.00),
}
"""USD per million tokens, input then output: OpenAI list prices on 2026-10-14."""


@dataclass(frozen=True)
class Candidate:
    """A model with the one sampling control it accepts."""

    model: str
    temperature: float | None = None
    effort: str | None = None

    @property
    def label(self) -> str:
        control = f"temperature {self.temperature}" if self.effort is None else self.effort
        return f"`{self.model}`, {control}"


CANDIDATES = [
    Candidate("gpt-4o-mini", temperature=0.0),
    Candidate("gpt-6-luna", effort="none"),
    Candidate("gpt-6-luna", effort="low"),
    Candidate("gpt-6-luna", effort="medium"),
    Candidate("gpt-6-sol", effort="none"),
]

FOLLOW_UP_GROUP = "multi_turn"
FOLLOW_UP_RUNS = 5
FOLLOW_UP_CANDIDATES = [
    Candidate("gpt-6-luna", effort="low"),
    Candidate("gpt-6-luna", effort="medium"),
]
"""Follow-ups are where effort could pay: the rewrite has to carry a referent across turns. They
get more runs, on the two efforts the router could use."""


class TimedProvider:
    """The production provider, with each call's wall time recorded."""

    def __init__(self, candidate: Candidate) -> None:
        self._provider = OpenAIProvider(
            api_key=get_settings().openai_api_key,
            model=candidate.model,
            temperature=candidate.temperature,
            reasoning_effort=candidate.effort,
        )
        self.seconds: list[float] = []

    async def structured(self, **call):
        start = time.perf_counter()
        try:
            return await self._provider.structured(**call)
        finally:
            self.seconds.append(time.perf_counter() - start)


@dataclass
class Result:
    """One candidate's numbers over every row and run."""

    candidate: Candidate
    hits: int
    calls: int
    seconds: list[float]
    tokens_in: int
    tokens_out: int
    misses: Counter

    def cost_per_thousand(self) -> float:
        price_in, price_out = PRICES[self.candidate.model]
        per_call = (self.tokens_in * price_in + self.tokens_out * price_out) / self.calls / 1e6
        return per_call * 1000


async def measure(candidate: Candidate, rows: list[dict], runs: int = RUNS) -> Result:
    """Route every row `runs` times and count agreement with the label."""
    provider = TimedProvider(candidate)
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(row: dict) -> tuple[dict, str]:
        async with gate:
            update = await route(row_state(row), provider, 6)
        return row, routed_as(row, update["route"])

    with get_usage_metadata_callback() as usage:
        outcomes = await asyncio.gather(*(one(row) for row in rows for _ in range(runs)))
    misses = Counter(
        f"{row['id']} (expected {row['expected_agent']}, got {got})"
        for row, got in outcomes
        if got != row["expected_agent"]
    )
    return Result(
        candidate=candidate,
        hits=len(outcomes) - sum(misses.values()),
        calls=len(outcomes),
        seconds=sorted(provider.seconds),
        tokens_in=sum(item["input_tokens"] for item in usage.usage_metadata.values()),
        tokens_out=sum(item["output_tokens"] for item in usage.usage_metadata.values()),
        misses=misses,
    )


def table(results: list[Result]) -> list[str]:
    """One line per candidate, then the rows each one missed."""
    lines = [
        "| Candidate | Accuracy | p50 latency | p90 latency | Tokens in / out per call "
        "| USD per 1k router calls |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        p90 = result.seconds[int(len(result.seconds) * 0.9) - 1]
        lines.append(
            f"| {result.candidate.label} | {result.hits}/{result.calls} "
            f"| {statistics.median(result.seconds):.1f} s | {p90:.1f} s "
            f"| {result.tokens_in // result.calls} / {result.tokens_out // result.calls} "
            f"| {result.cost_per_thousand():.2f} |"
        )
    lines += ["", "Misses, with how many of the runs missed:", ""]
    for result in results:
        missed = ", ".join(f"{row} ×{count}" for row, count in sorted(result.misses.items()))
        lines.append(f"- {result.candidate.label}: {missed or 'none'}")
    return lines


def render(results: list[Result], follow_ups: list[Result], rows: int, turns: int) -> str:
    """The committed report."""
    lines = [
        "# Router model benchmark",
        "",
        f"Every row of `{GOLDEN_SET.relative_to(Path.cwd())}` ({rows} rows) routed {RUNS} times "
        f"per candidate through router prompt v{load_prompt('router').version}, {CONCURRENCY} "
        f"calls at a time. A follow-up row counts as a hit only when the rewrite also keeps its "
        f"referent. Written by `uv run python -m evals.run_model_benchmark` on "
        f"{date.today().isoformat()}.",
        "",
        *table(results),
        "",
        "## Follow-up resolution",
        "",
        f"The {turns} `{FOLLOW_UP_GROUP}` rows, {FOLLOW_UP_RUNS} runs each, on the two efforts the "
        "router could use.",
        "",
        *table(follow_ups),
        "",
        "Prices are USD per million tokens, input / output, OpenAI list prices on 2026-10-14: "
        + "; ".join(f"`{model}` {i:.2f} / {o:.2f}" for model, (i, o) in PRICES.items())
        + ".",
        "",
    ]
    return "\n".join(lines)


async def main() -> None:
    rows = [row for row in load_rows() if row["expected_guardrail"] == "allow"]
    turns = [row for row in rows if row["group"] == FOLLOW_UP_GROUP]
    results = [await measure(candidate, rows) for candidate in CANDIDATES]
    follow_ups = [
        await measure(candidate, turns, FOLLOW_UP_RUNS) for candidate in FOLLOW_UP_CANDIDATES
    ]
    REPORT.write_text(render(results, follow_ups, len(rows), len(turns)), encoding="utf-8")
    print(REPORT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    asyncio.run(main())
