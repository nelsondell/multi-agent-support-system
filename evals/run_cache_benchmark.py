"""Prompt caching, measured: the routing golden set through the router with the explicit cache
breakpoint at the end of the static prompt, and without it, two passes each.

    uv run python -m evals.run_cache_benchmark

Real model calls, so it needs OPENAI_API_KEY and costs cents. It writes `evals/cache_report.md`:
for each mode, the share of input tokens read from cache, p50 and p90 latency, and cost per
thousand router calls at the configured LLM_PRICE_*. Like every eval, it never runs in pytest.
"""

import asyncio
import statistics
import time
from datetime import date
from pathlib import Path

from prometheus_client import REGISTRY

from app.graph.router import route
from app.prompts import load_prompt
from app.providers.llm import OpenAIProvider
from app.settings import get_settings
from evals.run_routing_eval import load_rows, row_state

REPORT = Path(__file__).parent / "cache_report.md"
PASSES = 2


def counter(name: str, labels: dict | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


async def measure(cache: bool) -> dict:
    """Route every row `PASSES` times through a provider with or without the breakpoint."""
    settings = get_settings()
    provider = OpenAIProvider(
        api_key=settings.openai_api_key,
        model=settings.llm_model,
        reasoning_effort=settings.llm_reasoning_effort,
        prices=(
            settings.llm_price_input,
            settings.llm_price_cached_input,
            settings.llm_price_output,
        ),
        cache=cache,
    )
    tokens = {
        kind: counter("support_model_tokens_total", {"kind": kind}) for kind in ("input", "cached")
    }
    cost, seconds = counter("support_model_cost_usd_total"), []
    rows = [row for row in load_rows() if row["expected_guardrail"] == "allow"]
    for _ in range(PASSES):
        for row in rows:
            started = time.perf_counter()
            await route(row_state(row), provider, window=settings.history_window)
            seconds.append(time.perf_counter() - started)
    inputs = counter("support_model_tokens_total", {"kind": "input"}) - tokens["input"]
    cached = counter("support_model_tokens_total", {"kind": "cached"}) - tokens["cached"]
    return {
        "calls": len(seconds),
        "cached_share": cached / inputs if inputs else 0.0,
        "p50": statistics.median(seconds),
        "p90": statistics.quantiles(seconds, n=10)[-1],
        "usd_per_1k": (counter("support_model_cost_usd_total") - cost) / len(seconds) * 1000,
    }


def render(results: dict[bool, dict]) -> str:
    settings = get_settings()
    lines = [
        "# Prompt cache benchmark",
        "",
        f"The {len(load_rows())}-row routing golden set through router prompt "
        f"v{load_prompt('router').version}, {PASSES} passes per mode, on "
        f"`{settings.llm_model}` at {settings.llm_reasoning_effort} effort. Written by "
        f"`uv run python -m evals.run_cache_benchmark` on {date.today().isoformat()}. Cost uses "
        f"LLM_PRICE_* (input {settings.llm_price_input}, cached {settings.llm_price_cached_input}, "
        f"output {settings.llm_price_output} USD per million tokens).",
        "",
        "| Mode | Calls | Input read from cache | p50 | p90 | USD per 1k router calls |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for cache, label in ((False, "implicit caching only"), (True, "explicit breakpoint")):
        row = results[cache]
        lines.append(
            f"| {label} | {row['calls']} | {row['cached_share']:.0%} | {row['p50']:.2f} s | "
            f"{row['p90']:.2f} s | {row['usd_per_1k']:.3f} |"
        )
    return "\n".join(lines) + "\n"


async def main() -> None:
    results = {cache: await measure(cache) for cache in (False, True)}
    REPORT.write_text(render(results), encoding="utf-8")
    print(REPORT.read_text())


if __name__ == "__main__":
    asyncio.run(main())
