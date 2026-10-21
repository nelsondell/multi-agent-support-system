"""Bounded replanning, measured against the router's plan as it is.

    uv run python -m evals.run_planning_eval [--runs 1]

`evals/planning_set.jsonl` holds 20 requests whose complete answer needs what one step finds: a
fee that depends on the merchant's terminal model, or a procedure for the state the account
shows. Each runs through the real graph twice: with `MAX_PLAN_CHANGES=0`, the baseline, which is
the router's plan as it is, and with the configured value. A request is completed when it is
answered without a person, carries one accepted spelling of every expected fact, and none of the
values that belong to another terminal model. The check is code; there is no judge.

Simple requests, the routing golden set's single-step rows outside the web, run the same way, so
the report shows whether replanning slows down requests that never needed it.

It calls the real chat model and embedder, so it needs OPENAI_API_KEY and costs cents. The day is
fixed at the one the mock account records describe. It writes `evals/planning_report.md`. Like
every eval, it never runs in pytest.
"""

import argparse
import asyncio
import statistics
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver

from app.checkpoints import checkpoint_serializer
from app.graph.build import build_graph
from app.graph.memory import thread_id, turn_input
from app.observability.metrics import metered
from app.providers.llm import build_provider
from app.rag.retrieve import HybridRetriever
from app.settings import Settings, get_settings
from app.tools.account import RECORDED_ON
from evals.run_judge import revision
from evals.run_routing_eval import load_rows

PLANNING_SET = Path(__file__).parent / "planning_set.jsonl"
REPORT = Path(__file__).parent / "planning_report.md"
CALLER = "cliente1988"


@dataclass
class Run:
    """One request through one mode."""

    row_id: str
    mode: str
    simple: bool
    outcome: str
    completed: bool
    steps: int
    calls: int
    cost_usd: float
    seconds: float


def simple_rows() -> list[dict]:
    """The golden set's single-step requests that never touch the web."""
    return [
        row
        for row in load_rows()
        if row["expected_guardrail"] == "allow"
        and row["expected_intent"] != "general_info"
        and row["expected_agent"] in ("knowledge", "support")
        and not row.get("history")
    ]


def completed(answer: str, row: dict) -> bool:
    """Every expected fact in one of its spellings, and no value of another terminal model."""
    text = answer.lower()
    found = all(any(option.lower() in text for option in group) for group in row["expect"])
    return found and not any(value.lower() in text for value in row["reject"])


async def run_row(graph, row: dict, mode: str, simple: bool) -> Run:
    """One fresh conversation, metered like a `/chat` turn."""
    caller, conversation = row.get("caller", CALLER), uuid4().hex
    turn = turn_input(
        message=row["message"],
        user_id=caller,
        conversation_id=conversation,
        trace_id="planning-eval",
        today=RECORDED_ON,
    )
    config = {"configurable": {"thread_id": thread_id(caller, conversation)}}
    started = time.perf_counter()
    with metered() as spend:
        final = await graph.ainvoke(turn, config)
    escalation = final.get("escalation")
    answered = escalation is None
    return Run(
        row_id=row["id"],
        mode=mode,
        simple=simple,
        outcome="answered" if answered else f"escalated ({escalation.reason.value})",
        completed=answered and not simple and completed(final.get("answer") or "", row),
        steps=len(final.get("step_results", [])),
        calls=spend.model_calls,
        cost_usd=spend.cost_usd,
        seconds=time.perf_counter() - started,
    )


async def run_mode(settings: Settings, retriever: HybridRetriever, mode: str) -> list[Run]:
    """Every dependent and simple request through one graph."""
    checkpointer = InMemorySaver(serde=checkpoint_serializer())
    graph = build_graph(build_provider(settings), settings, retriever, checkpointer)
    runs = [await run_row(graph, row, mode, False) for row in load_rows(PLANNING_SET)]
    return runs + [await run_row(graph, row, mode, True) for row in simple_rows()]


def p95(values: list[float]) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[94]


def summary(label: str, runs: list[Run]) -> str:
    """One mode's line: completion, steps, calls, latency and cost."""
    dependent = [run for run in runs if not run.simple]
    seconds = [run.seconds for run in dependent]
    simple = [run.seconds for run in runs if run.simple]
    return (
        f"| {label} | {sum(run.completed for run in dependent)}/{len(dependent)} | "
        f"{statistics.mean(run.steps for run in dependent):.2f} | "
        f"{statistics.mean(run.calls for run in dependent):.2f} | "
        f"{statistics.median(seconds):.2f} s | {p95(seconds):.2f} s | {p95(simple):.2f} s | "
        f"{statistics.mean(run.cost_usd for run in dependent):.5f} |"
    )


def per_request(results: dict[str, list[Run]]) -> list[str]:
    """Each dependent request's outcome in every mode."""
    lines = [
        "| Id | Request | " + " | ".join(results) + " |",
        "| --- | --- |" + " --- |" * len(results),
    ]
    for row in load_rows(PLANNING_SET):
        cells = []
        for runs in results.values():
            mine = [run for run in runs if run.row_id == row["id"]]
            done = sum(run.completed for run in mine)
            cells.append(f"{done}/{len(mine)} completed; {', '.join(r.outcome for r in mine)}")
        lines.append(f"| {row['id']} | {row['message']} | " + " | ".join(cells) + " |")
    return lines


def render(results: dict[str, list[Run]], settings: Settings, runs: int) -> str:
    dependent = len(load_rows(PLANNING_SET))
    header = [
        "# Planning eval",
        "",
        f"Written by `uv run python -m evals.run_planning_eval --runs {runs}` on "
        f"{date.today().isoformat()}, at {revision()}. {dependent} requests that depend on a "
        f"finding and {len(simple_rows())} simple ones, {runs} run(s) each per mode, on "
        f"`{settings.llm_model}` at {settings.llm_reasoning_effort} effort; the day fixed at "
        f"{RECORDED_ON.isoformat()}, the day the mock account records describe. Completion is "
        "checked in code (`evals/planning_set.jsonl`). Latency and cost are per request.",
        "",
        "| Mode | Task completion | Steps | Model calls | p50 | p95 | p95, simple requests | USD |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines = [summary(label, mode_runs) for label, mode_runs in results.items()]
    return "\n".join([*header, *lines, "", "## Per request", "", *per_request(results)]) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=1, help="independent runs per mode")
    arguments = parser.parse_args()
    settings = get_settings()
    retriever = HybridRetriever.from_settings(settings)
    modes = {
        "baseline (MAX_PLAN_CHANGES=0)": settings.model_copy(update={"max_plan_changes": 0}),
        f"replanning (MAX_PLAN_CHANGES={settings.max_plan_changes})": settings,
    }
    results: dict[str, list[Run]] = {label: [] for label in modes}
    for _ in range(arguments.runs):
        for label, mode in modes.items():
            results[label] += await run_mode(mode, retriever, label)
    REPORT.write_text(render(results, settings, arguments.runs), encoding="utf-8")
    print(REPORT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    asyncio.run(main())
