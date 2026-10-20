"""The offline half of `make verify`: everything after the frozen sync, with no key and no network.

    make verify            # uv sync --frozen, then this
    make verify-offline    # this alone, in an environment already synced

Each check runs as a child process whose environment carries no API key or token, and must exit
0. The tests run with sockets blocked (pyproject), and a blocked attempt counts as a failure even
when the test around it passed. The results go to reports/verify.md; the exit code is non-zero
when any check failed. Only the standard library is used, so the report can always be written.
"""

import os
import platform
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "reports" / "verify.md"
SECRET = re.compile(r".*(_API_KEY|_TOKEN|_SECRET)$")
BLOCKED_SOCKET = "tried to use socket"


@dataclass(frozen=True)
class Check:
    """One command, how it is shown in the report, and the output line that summarises it."""

    name: str
    command: list[str]
    summary: re.Pattern


CHECKS = [
    Check("Lint", ["ruff", "check", "."], re.compile(r"All checks passed!")),
    Check("Format", ["ruff", "format", "--check", "."], re.compile(r"\d+ files? already \w+")),
    Check("Offline tests", ["pytest", "-q"], re.compile(r"\d+ passed.*")),
    Check(
        "Guardrail eval",
        ["python", "-m", "evals.run_routing_eval", "--guardrail-only"],
        re.compile(r"overall: .*"),
    ),
]


@dataclass(frozen=True)
class Result:
    check: Check
    passed: bool
    summary: str
    seconds: float


def keyless_environment() -> dict[str, str]:
    """This process's environment minus every key, token and secret."""
    return {name: value for name, value in os.environ.items() if not SECRET.match(name)}


def argv(command: list[str]) -> list[str]:
    """The command under this interpreter, so it runs the synced environment's own tools."""
    if command[0] == "python":
        return [sys.executable, *command[1:]]
    return [sys.executable, "-m", *command]


def run(check: Check, env: dict[str, str]) -> Result:
    """Run one check from the repository root and summarise it from its own output."""
    started = time.monotonic()
    done = subprocess.run(argv(check.command), cwd=ROOT, env=env, capture_output=True, text=True)
    seconds = time.monotonic() - started
    output = done.stdout + done.stderr
    print(output, end="" if output.endswith("\n") else "\n")
    found = check.summary.search(output)
    summary = found.group(0).strip() if found else f"exit {done.returncode}"
    if BLOCKED_SOCKET in output:
        return Result(check, False, f"{summary}; a network attempt was blocked", seconds)
    return Result(check, done.returncode == 0, summary, seconds)


def network_state() -> str:
    """Whether the network was reachable while the checks ran, which none of them needs."""
    try:
        socket.create_connection(("pypi.org", 443), timeout=3).close()
    except OSError as exc:
        return f"unreachable ({type(exc).__name__})"
    return "reachable, and unused: the tests run with sockets blocked"


def git(*args: str) -> str:
    """Git's answer, or nothing where there is no git or no repository (a `git archive` copy)."""
    try:
        done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    except FileNotFoundError:
        return ""
    return done.stdout.strip()


def revision() -> str:
    """The commit measured, flagged when the working tree held more than it."""
    sha = git("rev-parse", "--short", "HEAD")
    if not sha:
        return "on a copy of the tree with no git history"
    return f"at `{sha}`" + (" with uncommitted changes" if git("status", "--porcelain") else "")


def render(results: list[Result], network: str) -> str:
    """The committed report: the environment, then one row per check."""
    sync = os.environ.get("VERIFY_SYNC_SECONDS")
    total = sum(result.seconds for result in results)
    lines = [
        "# Verify report",
        "",
        f"`make verify` on {date.today().isoformat()}, {revision()}: {platform.system()} "
        f"{platform.machine()}, Python {platform.python_version()}.",
        "",
        f"- Network during the checks: {network}.",
        "- Keys in the checks' environment: none (every `*_API_KEY`, `*_TOKEN` and `*_SECRET` "
        "is removed).",
        f"- `.env`: {'present, and read by no check' if (ROOT / '.env').exists() else 'absent'}.",
        "",
        "| Check | Command | Result | Time |",
        "| --- | --- | --- | --- |",
    ]
    if sync:
        lines.append(f"| Sync | `uv sync --frozen` | done, with the network | {sync} s |")
    for result in results:
        outcome = result.summary if result.passed else f"**failed**: {result.summary}"
        command = " ".join(result.check.command)
        lines.append(f"| {result.check.name} | `{command}` | {outcome} | {result.seconds:.1f} s |")
    verdict = "all checks passed" if all(r.passed for r in results) else "**a check failed**"
    lines += ["", f"{verdict}, in {total:.1f} s after the sync.", ""]
    return "\n".join(lines)


def main() -> int:
    env = keyless_environment()
    network = network_state()
    results = [run(check, env) for check in CHECKS]
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(render(results, network), encoding="utf-8")
    print(f"\nwrote {REPORT.relative_to(ROOT)}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
