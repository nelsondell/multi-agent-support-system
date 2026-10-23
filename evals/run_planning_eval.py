"""Bounded replanning against the router's plan as it is (`MAX_PLAN_CHANGES=0`).

    uv run python -m evals.run_planning_eval

The 20 requests in `evals/planning_set.jsonl` need what one step finds. One is completed when it is
answered without a person, carries every expected fact and no value of another terminal model;
the check is code. The golden set's simple single-step rows run too, for their p95. Real model and
embedder calls (OPENAI_API_KEY, cents), on the day the mock records describe; never in pytest.
Writes `evals/planning_report.md`.
"""

import asyncio
import statistics
import time
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
from app.settings import get_settings
from app.tools.account import RECORDED_ON
from evals.run_judge import revision
from evals.run_routing_eval import load_rows

PLANNING_SET = Path(__file__).parent / "planning_set.jsonl"
REPORT = Path(__file__).parent / "planning_report.md"


def simple_rows() -> list[dict]:
    return [
        row
        for row in load_rows()
        if row["expected_guardrail"] == "allow"
        and row["expected_intent"] != "general_info"
        and row["expected_agent"] in ("knowledge", "support")
        and not row.get("history")
    ]


def completed(final: dict, row: dict) -> bool:
    text = (final.get("answer") or "").lower()
    found = all(any(option.lower() in text for option in group) for group in row["expect"])
    rejected = any(value.lower() in text for value in row["reject"])
    return final.get("escalation") is None and found and not rejected


async def measure(graph, row: dict) -> dict:
    """One fresh conversation, metered like a `/chat` turn."""
    caller, conversation = row.get("caller", "cliente1988"), uuid4().hex
    turn = turn_input(
        message=row["message"],
        user_id=caller,
        conversation_id=conversation,
        trace_id="eval",
        today=RECORDED_ON,
    )
    started = time.perf_counter()
    with metered() as spend:
        final = await graph.ainvoke(
            turn, {"configurable": {"thread_id": thread_id(caller, conversation)}}
        )
    return {
        "done": "expect" in row and completed(final, row),
        "steps": len(final["step_results"]),
        "calls": spend.model_calls,
        "cost": spend.cost_usd,
        "seconds": time.perf_counter() - started,
    }


def line(label: str, dependent: list[dict], simple: list[dict]) -> str:
    def p95(runs: list[dict]) -> float:
        return statistics.quantiles([run["seconds"] for run in runs], n=100, method="inclusive")[94]

    mean = {
        key: statistics.mean(run[key] for run in dependent) for key in ("steps", "calls", "cost")
    }
    done = f"{sum(run['done'] for run in dependent)}/{len(dependent)}"
    cells = [label, done, f"{mean['steps']:.2f}", f"{mean['calls']:.2f}"]
    cells += [f"{p95(dependent):.2f} s", f"{p95(simple):.2f} s", f"{mean['cost']:.5f}"]
    return "| " + " | ".join(cells) + " |"


async def main() -> None:
    settings = get_settings()
    retriever, rows = HybridRetriever.from_settings(settings), load_rows(PLANNING_SET)
    table = [
        "| Mode | Completed | Steps | Model calls | p95 | p95, simple | USD |",
        "|---" * 7 + "|",
    ]
    for changes in (0, settings.max_plan_changes):
        mode = settings.model_copy(update={"max_plan_changes": changes})
        graph = build_graph(
            build_provider(mode), mode, retriever, InMemorySaver(serde=checkpoint_serializer())
        )
        dependent = [await measure(graph, row) for row in rows]
        simple = [await measure(graph, row) for row in simple_rows()]
        table.append(line(f"MAX_PLAN_CHANGES={changes}", dependent, simple))
    REPORT.write_text(
        f"# Planning eval\n\n`uv run python -m evals.run_planning_eval` on {date.today()}, at "
        f"{revision()}, on `{settings.llm_model}`: {len(rows)} requests that depend on a finding "
        f"and {len(simple_rows())} simple ones, latency and cost per request.\n\n"
        + "\n".join(table)
        + "\n",
        encoding="utf-8",
    )
    print(REPORT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    asyncio.run(main())
