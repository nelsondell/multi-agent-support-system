"""The offline half of `make verify`: every check after the frozen sync, with no key and no network.

Each check runs without any `*_API_KEY`, `*_TOKEN` or `*_SECRET` in its environment and must exit
0; a socket the tests blocked fails it even when the test around it passed. The report goes to
reports/verify.md.
"""

import os
import platform
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "reports" / "verify.md"
SECRET = re.compile(r".*(_API_KEY|_TOKEN|_SECRET)$")
CHECKS = [  # name, module and arguments, the output line that summarises it
    ("Lint", "ruff check .", r"All checks passed!"),
    ("Format", "ruff format --check .", r"\d+ files? already \w+"),
    ("Offline tests", "pytest -q", r"\d+ passed.*"),
    ("Guardrail eval", "evals.run_routing_eval --guardrail-only", r"overall: .*"),
]


def run(command: str, pattern: str, env: dict[str, str]) -> tuple[bool, str, float]:
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-m", *command.split()], cwd=ROOT, env=env, capture_output=True, text=True
    )
    output = done.stdout + done.stderr
    print(output)
    found = re.search(pattern, output)
    summary = found.group(0).strip() if found else f"exit {done.returncode}"
    if "tried to use socket" in output:
        return False, f"{summary}; a network attempt was blocked", time.monotonic() - started
    return done.returncode == 0, summary, time.monotonic() - started


def revision() -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout

    sha = git("rev-parse", "--short", "HEAD").strip()
    if not sha:
        return "on a copy of the tree with no git history"
    return f"at `{sha}`" + (" with uncommitted changes" if git("status", "--porcelain") else "")


def report(results: list[tuple]) -> str:
    sync = os.environ.get("VERIFY_SYNC_SECONDS")
    rows = [f"| Sync | `uv sync --frozen` | done | {sync} s |"] if sync else []
    for name, command, passed, summary, seconds in results:
        outcome = summary if passed else f"**failed**: {summary}"
        rows.append(f"| {name} | `{command}` | {outcome} | {seconds:.1f} s |")
    verdict = "all checks passed" if all(row[2] for row in results) else "**a check failed**"
    total = sum(row[4] for row in results)
    header = (
        f"`make verify` on {date.today().isoformat()}, {revision()}: {platform.system()} "
        f"{platform.machine()}, Python {platform.python_version()}, no key in any check's "
        "environment."
    )
    table = ["| Check | Command | Result | Time |", "| --- | --- | --- | --- |", *rows]
    footer = f"{verdict}, in {total:.1f} s after the sync."
    return "\n".join(["# Verify report", "", header, "", *table, "", footer, ""])


def main() -> int:
    env = {name: value for name, value in os.environ.items() if not SECRET.match(name)}
    results = [(name, command, *run(command, pattern, env)) for name, command, pattern in CHECKS]
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(report(results), encoding="utf-8")
    print(f"wrote {REPORT.relative_to(ROOT)}")
    return 0 if all(row[2] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
