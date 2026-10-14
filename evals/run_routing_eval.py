"""Routing golden set runner.

Deliberately outside pytest: this is a measurement, not a test. A statistical score that drops
one case in fifteen is a signal to read, not a red build.

    uv run python -m evals.run_routing_eval

Rows may carry earlier turns (`history`) and words the rewrite must keep (`expected_mentions`),
which is how follow-ups are scored: the right agent with a query that lost its referent is a miss.

The guardrail half is deterministic and always runs. The router half calls the configured model:
with `LLM_BACKEND=openai` it needs a key and costs money; with `LLM_BACKEND=fake` it runs offline,
which proves the runner end to end but measures nothing about routing quality.
"""

import argparse
import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from app.graph.guardrail import classify
from app.graph.router import RouterDecision, route
from app.graph.state import GraphState
from app.providers.llm import build_provider
from app.settings import get_settings

GOLDEN_SET = Path(__file__).parent / "routing_golden_set.jsonl"


@dataclass
class Outcome:
    """One row's result on one of the two stages."""

    row_id: str
    group: str
    expected: str
    actual: str

    @property
    def hit(self) -> bool:
        """Whether the system agreed with the label."""
        return self.expected == self.actual


def load_rows(path: Path = GOLDEN_SET) -> list[dict]:
    """Read the labelled set."""
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def evaluate_guardrail(rows: list[dict]) -> list[Outcome]:
    """Score the deterministic stage: scope and identity, before any model call."""
    return [
        Outcome(
            row_id=row["id"],
            group=row["group"],
            expected=row["expected_guardrail"],
            actual=classify(row["message"]).value,
        )
        for row in rows
    ]


def row_state(row: dict) -> GraphState:
    """The turn a row describes: its message, after its earlier turns when it has any."""
    kinds = {"human": HumanMessage, "ai": AIMessage}
    history = [kinds[item["role"]](content=item["content"]) for item in row.get("history", [])]
    return GraphState(message=row["message"], user_id="eval", messages=history)


def routed_as(row: dict, decision: RouterDecision) -> str:
    """What the router made of a row, in the label's terms: the plan's agents, plus any expected
    mention the rewrite lost. A mention lists its accepted spellings separated by "|"."""
    agents = ",".join(step.agent.value for step in decision.steps)
    rewrite = " ".join(step.query_pt for step in decision.steps).lower()
    lost = [
        mention
        for mention in row.get("expected_mentions", [])
        if not any(spelling.lower() in rewrite for spelling in mention.split("|"))
    ]
    return agents + (f" (lost {', '.join(lost)})" if lost else "")


async def evaluate_router(rows: list[dict]) -> list[Outcome]:
    """Score the model stage on the rows the guardrail is supposed to let through."""
    provider = build_provider(get_settings())
    outcomes = []
    for row in rows:
        if row["expected_guardrail"] != "allow":
            continue
        update = await route(row_state(row), provider, window=get_settings().history_window)
        outcomes.append(
            Outcome(
                row_id=row["id"],
                group=row["group"],
                expected=row["expected_agent"],
                actual=routed_as(row, update["route"]),
            )
        )
    return outcomes


def accuracy(outcomes: list[Outcome]) -> float:
    """Share of rows the system got right."""
    return sum(outcome.hit for outcome in outcomes) / len(outcomes) if outcomes else 0.0


def report(title: str, outcomes: list[Outcome]) -> None:
    """Print overall accuracy, then a breakdown per expected class and per group."""
    print(f"\n{title}")
    print(f"  overall: {accuracy(outcomes):.1%}  ({sum(o.hit for o in outcomes)}/{len(outcomes)})")

    for label, key in (("by expected class", "expected"), ("by group", "group")):
        buckets: dict[str, list[Outcome]] = defaultdict(list)
        for outcome in outcomes:
            buckets[getattr(outcome, key)].append(outcome)
        print(f"  {label}:")
        for name in sorted(buckets):
            bucket = buckets[name]
            print(
                f"    {name:16} {accuracy(bucket):6.1%}  "
                f"({sum(o.hit for o in bucket)}/{len(bucket)})"
            )

    misses = [outcome for outcome in outcomes if not outcome.hit]
    if misses:
        print("  misses:")
        for outcome in misses:
            print(f"    {outcome.row_id}  expected={outcome.expected}  got={outcome.actual}")


def main() -> None:
    """Run whichever stages the environment allows."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--guardrail-only", action="store_true", help="skip the stage that calls a model"
    )
    arguments = parser.parse_args()

    rows = load_rows()
    print(f"golden set: {len(rows)} rows from {GOLDEN_SET}")
    report("guardrail (deterministic)", evaluate_guardrail(rows))

    if arguments.guardrail_only:
        print("\nrouter: skipped (--guardrail-only)")
        return
    settings = get_settings()
    if settings.llm_backend == "openai" and not settings.openai_api_key:
        print("\nrouter: skipped, OPENAI_API_KEY is not set (this stage calls a real model)")
        return
    print(f"\nrouter backend: {settings.llm_backend}")
    report("router (model)", asyncio.run(evaluate_router(rows)))


if __name__ == "__main__":
    main()
