"""The evals that gate PRs: one check per failure mode on the 100 sampled requests, a 40-case red
team, and the judge's TPR and TNR against the labelled traces.

    uv run python -m evals.run_evals           # replay the recording: no key, no network (CI)
    uv run python -m evals.run_evals --live    # real model, retrieval, web and judge; records
    uv run python -m evals.run_evals --update  # live only for what the recording lacks

Reports the pass-all rate and each check's rate, never a weighted score, and exits non-zero when a
rate falls below `evals/thresholds.toml`. A live run rewrites `evals/recording.jsonl` and
`evals/eval_report.md`.
"""

import argparse
import asyncio
import csv
import hashlib
import json
import sys
import tomllib
from datetime import date
from pathlib import Path

import app.main
from app.graph.memory import thread_id
from app.graph.state import GraphState
from evals.checks import TRACE_CHECKS, answer_of, judge_missing, missing_info, safe
from evals.harness import PLANTED, Recording, open_api, recorded_judge
from evals.run_judge import revision
from scripts.dev_auth import token

HERE = Path(__file__).parent
REQUESTS, RED_TEAM = HERE / "trace_requests.jsonl", HERE / "red_team.jsonl"
TRACES, LABELS = HERE / "traces.csv", HERE / "labels.csv"
RECORDING, REPORT, THRESHOLDS = (
    HERE / "recording.jsonl",
    HERE / "eval_report.md",
    HERE / "thresholds.toml",
)
LIMIT = asyncio.Semaphore(6)
MIN_POSITIVES = 10
"""Held-out failures below which a judge's TPR is reported as not validated."""


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


async def converse(http, api, case: dict) -> tuple[dict, GraphState]:
    """Send a case's turns as its merchant, with any planted page; return the last reply."""
    planted = PLANTED.set(case.get("plant"))
    try:
        headers = {"Authorization": f"Bearer {token('merchant', case['merchant'], 3600)}"}
        for n, message in enumerate(case["turns"], start=1):
            body = {"message": message, "conversation_id": case["id"]}
            if n == len(case["turns"]):
                body |= case.get("body", {}) | (
                    {"confirm_action_id": case["confirm"]} if "confirm" in case else {}
                )
            response = await http.post("/chat", json=body, headers=headers)
        reply = (
            response.json()
            if response.status_code == 200
            else {"status": f"HTTP {response.status_code}"}
        )
        config = {"configurable": {"thread_id": thread_id(case["merchant"], case["id"])}}
        values = (await api.state.graph.aget_state(config)).values
        return reply, GraphState.model_validate(values or {"message": "", "user_id": ""})
    finally:
        PLANTED.reset(planted)


async def run_suite(http, api, judge, cases: list[dict], red_team: bool) -> list[dict]:
    async def one(case: dict) -> dict:
        async with LIMIT:
            reply, state = await converse(http, api, case)
            if red_team:
                return {"id": case["id"], "checks": {"safe": safe(case, reply, state)}}
            checks = {name: check(case, reply, state) for name, check in TRACE_CHECKS.items()}
            checks["missing_info"] = await missing_info(case, reply, state, judge)
            return {"id": case["id"], "checks": checks}

    return list(await asyncio.gather(*(one(case) for case in cases)))


async def validate_judge(judge) -> dict[str, tuple[int, int, int, int]]:
    """The judge on the labelled traces, per stratified split: TP, FN, TN, FP."""
    traces = {row["id"]: row for row in csv.DictReader(TRACES.open(encoding="utf-8-sig"))}
    labels = list(csv.DictReader(LABELS.open(encoding="utf-8")))
    truth = {row["id"]: "missing_info" in (row["mode"], row["also"]) for row in labels}

    async def verdict(case_id: str) -> tuple[str, bool]:
        row = traces[case_id]
        answer = answer_of({"answer": row["answer"]})
        return case_id, (await judge_missing(row["message"], answer, row["evidence"], judge)).fails

    judged = dict(await asyncio.gather(*(verdict(case_id) for case_id in truth)))
    splits: dict[str, list[str]] = {"train": [], "dev": [], "test": []}
    for label in (True, False):
        group = sorted(
            (i for i in truth if truth[i] is label),
            key=lambda i: hashlib.sha256(i.encode()).hexdigest(),
        )
        cut1, cut2 = round(len(group) * 0.4), round(len(group) * 0.7)
        splits["train"] += group[:cut1]
        splits["dev"] += group[cut1:cut2]
        splits["test"] += group[cut2:]
    count = lambda ids, t, j: sum(truth[i] is t and judged[i] is j for i in ids)  # noqa: E731
    return {
        name: (
            count(ids, True, True),
            count(ids, True, False),
            count(ids, False, False),
            count(ids, False, True),
        )
        for name, ids in splits.items()
    }


def rates(results: list[dict]) -> dict[str, tuple[int, int]]:
    """Each check's (passed, applicable), and `pass_all`: cases with no applicable check failing."""
    names = results[0]["checks"]
    table = {
        name: (
            sum(r["checks"][name] is True for r in results),
            sum(r["checks"][name] is not None for r in results),
        )
        for name in names
    }
    table["pass_all"] = (
        sum(all(v is not False for v in r["checks"].values()) for r in results),
        len(results),
    )
    return table


def below(suites: dict[str, dict], thresholds: dict) -> list[str]:
    return [
        f"{suite}.{name}: {passed}/{total} < {thresholds[suite][name]:.2f}"
        for suite, table in suites.items()
        for name, (passed, total) in table.items()
        if name in thresholds.get(suite, {}) and total and passed / total < thresholds[suite][name]
    ]


def render(suites: dict, failing: dict, validation: dict, today: date) -> str:
    lines = [
        "# Eval report",
        "",
        f"`uv run python -m evals.run_evals --live` on {today}, at {revision()}. Replay it offline "
        "with `make eval-offline`; CI does, and fails below `evals/thresholds.toml`.",
    ]
    for suite, table in suites.items():
        lines += [
            "",
            f"## {suite}",
            "",
            "| Check | Passed | Rate | Failing |",
            "| --- | --- | --- | --- |",
        ]
        for name, (passed, total) in table.items():
            ids = ", ".join(failing[suite].get(name, [])) or "—"
            lines.append(
                f"| {name} | {passed}/{total} | {passed / total:.0%} | {ids} |"
                if total
                else f"| {name} | 0/0 | — | — |"
            )
    lines += [
        "",
        "## Judge: missing_info, against the labels",
        "",
        "| Split | TPR | TNR |",
        "| --- | --- | --- |",
    ]
    for split, (tp, fn, tn, fp) in validation.items():
        tpr = f"{tp}/{tp + fn}" if tp + fn else "—"
        lines.append(f"| {split} | {tpr} | {tn}/{tn + fp} |")
    positives = sum(tp + fn for tp, fn, _, _ in validation.values())
    held_out = sum(validation["test"][:2])
    if held_out < MIN_POSITIVES:
        lines += [
            "",
            f"**Not validated.** The labels hold {positives} failures of this mode, {held_out} of "
            f"them in the held-out split: a TPR needs at least {MIN_POSITIVES} there. The judge "
            "runs as a regression check only.",
        ]
    return "\n".join(lines) + "\n"


async def main(live: bool, update: bool) -> int:
    recording = Recording(RECORDING, live or update, reuse=update)
    live = live or update
    if live and "meta:today" not in recording.calls:
        recording.calls["meta:today"] = app.main.merchant_today().isoformat()
    today = date.fromisoformat(recording.calls["meta:today"])
    judge = recorded_judge(recording)
    async with open_api(recording, today) as (api, http):
        traces = await run_suite(http, api, judge, load(REQUESTS), red_team=False)
        red_team = await run_suite(http, api, judge, load(RED_TEAM), red_team=True)
    validation = await validate_judge(judge)
    results = {"traces": traces, "red_team": red_team}
    suites = {suite: rates(rows) for suite, rows in results.items()}
    failing = {
        s: {n: [r["id"] for r in rows if r["checks"].get(n) is False] for n in suites[s]}
        for s, rows in results.items()
    }
    for suite, table in suites.items():
        print(suite, " ".join(f"{name} {p}/{t}" for name, (p, t) in table.items()))
    totals = {suite: table["pass_all"] for suite, table in suites.items()}
    print("pass-all " + ", ".join(f"{suite} {p}/{t}" for suite, (p, t) in totals.items()))
    if live:
        recording.save()
        REPORT.write_text(render(suites, failing, validation, today), encoding="utf-8")
    problems = below(suites, tomllib.loads(THRESHOLDS.read_text()) if THRESHOLDS.is_file() else {})
    for problem in problems:
        print(f"below threshold: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="call the real vendors and record them")
    parser.add_argument("--update", action="store_true", help="live only where the recording lacks")
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(main(arguments.live, arguments.update)))
