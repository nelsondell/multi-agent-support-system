"""The held-out set against the owner's blind labels: pass rate, and the missing_info judge.

    uv run python -m evals.run_heldout            # replays the recorded judge: no key, no network
    uv run python -m evals.run_heldout --live     # asks the judge again (ANTHROPIC_API_KEY)
    uv run python -m evals.run_heldout --update   # asks only what the recording lacks

Reads evals/heldout_requests.jsonl, heldout_traces.csv and heldout_labels.csv (labelled by the
owner, blind, no model draft) and writes evals/heldout_report.md. A row with no label is left out
and named. The judge is validated only with at least MIN_FAILURES labelled failures of its mode and
both TPR and TNR at MIN_RATE or more.
"""

import argparse
import asyncio
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path

from evals.checks import answer_of, judge_missing
from evals.harness import Recording, recorded_judge

HERE = Path(__file__).parent
REQUESTS = HERE / "heldout_requests.jsonl"
TRACES = HERE / "heldout_traces.csv"
LABELS = HERE / "heldout_labels.csv"
RECORDING = HERE / "heldout_recording.jsonl"
REPORT = HERE / "heldout_report.md"
MIN_FAILURES, MIN_RATE = 10, 0.8


def rate(ids: list[str], labels: dict[str, dict]) -> str:
    passed = sum(labels[i]["pass_fail"] == "pass" for i in ids)
    return f"{passed}/{len(ids)} ({passed / len(ids):.0%})"


async def verdicts(ids: list[str], traces: dict[str, dict], judge) -> dict[str, bool]:
    """Whether the judge says each answer calls missing what its evidence has."""

    async def one(i: str) -> tuple[str, bool]:
        row = traces[i]
        answer = answer_of({"answer": row["answer"]})
        return i, (await judge_missing(row["message"], answer, row["evidence"], judge)).fails

    return dict(await asyncio.gather(*(one(i) for i in ids)))


def validation(ids: list[str], labels: dict[str, dict], judged: dict[str, bool]) -> list[str]:
    """The judge against the labels: counts, rates and the verdict."""
    truth = {i: labels[i]["mode"] == "missing_info" for i in ids}
    tp = sum(truth[i] and judged[i] for i in ids)
    fn = sum(truth[i] and not judged[i] for i in ids)
    tn = sum(not truth[i] and not judged[i] for i in ids)
    fp = sum(not truth[i] and judged[i] for i in ids)
    tpr = f"{tp}/{tp + fn}" if tp + fn else "— (no labelled failure of this mode)"
    tnr = f"{tn}/{tn + fp}"
    valid = (
        tp + fn >= MIN_FAILURES
        and tp / (tp + fn) >= MIN_RATE
        and tn + fp > 0
        and tn / (tn + fp) >= MIN_RATE
    )
    verdict = "**validated**" if valid else "**not validated**"
    return [
        "| Judge | TP | FN | TN | FP | TPR | TNR |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        f"| `missing_info` | {tp} | {fn} | {tn} | {fp} | {tpr} | {tnr} |",
        "",
        f"{verdict}: it needs at least {MIN_FAILURES} labelled `missing_info` failures and both "
        f"rates at {MIN_RATE} or more; the labels hold {tp + fn}.",
    ]


def render(labels: dict[str, dict], requests: dict[str, dict], judged: dict[str, bool]) -> str:
    labelled = [i for i in labels if labels[i]["pass_fail"] in ("pass", "fail")]
    unlabelled = [i for i in labels if i not in labelled]
    bait = [i for i in labelled if requests[i].get("bait")]
    rest = [i for i in labelled if not requests[i].get("bait")]
    modes = Counter(labels[i]["mode"] for i in labelled if labels[i]["pass_fail"] == "fail")
    lines = [
        "# Held-out report",
        "",
        f"`uv run python -m evals.run_heldout` on {date.today().isoformat()}. "
        f"{len(labelled)} of the {len(labels)} requests in `evals/{REQUESTS.name}`, labelled by "
        "the owner, blind, no model draft (`evals/heldout_labels.csv`)"
        + (f"; left out, with no label: {', '.join(unlabelled)}." if unlabelled else "."),
        "",
        "| Slice | Pass |",
        "| --- | --- |",
        f"| all labelled | {rate(labelled, labels)} |",
        f"| failure mode 4 bait | {rate(bait, labels)} |",
        f"| the rest | {rate(rest, labels)} |",
        "",
        "Failures by the labeller's mode: "
        + ", ".join(f"{mode} {count}" for mode, count in modes.most_common())
        + ".",
        "",
        *validation(labelled, labels, judged),
    ]
    return "\n".join(lines) + "\n"


async def main(live: bool, update: bool) -> None:
    labels = {row["id"]: row for row in csv.DictReader(LABELS.open(encoding="utf-8"))}
    traces = {row["id"]: row for row in csv.DictReader(TRACES.open(encoding="utf-8-sig"))}
    requests = {r["id"]: r for r in map(json.loads, REQUESTS.read_text("utf-8").splitlines())}
    recording = Recording(RECORDING, live or update, reuse=update)
    labelled = [i for i in labels if labels[i]["pass_fail"] in ("pass", "fail")]
    judged = await verdicts(labelled, traces, recorded_judge(recording))
    if live or update:
        recording.save()
    REPORT.write_text(render(labels, requests, judged), encoding="utf-8")
    print(REPORT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="ask the judge, and record it")
    parser.add_argument("--update", action="store_true", help="ask only what the recording lacks")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.live, arguments.update))
