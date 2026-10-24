"""Error analysis, step 1: run 100 varied requests through the real API and export masked traces.

    uv run python scripts/sample_traces.py [--limit N]

Each request in evals/trace_requests.jsonl goes through `/chat` as its merchant, earlier turns
first, on a throwaway state directory. The last turn's reply and what the graph recorded for it
go to evals/traces.csv, masked, with empty `pass_fail` and `note` columns for a person to fill
in. Needs OPENAI_API_KEY (TAVILY_API_KEY for general questions) and `make dev-keys`; costs cents.
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx
from prometheus_client import REGISTRY

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.graph.memory import thread_id  # noqa: E402
from app.graph.state import GraphState  # noqa: E402
from app.main import create_app  # noqa: E402
from app.observability import configure_logging, mask_pii  # noqa: E402
from scripts.dev_auth import PUBLIC, keys, token  # noqa: E402

REQUESTS = ROOT / "evals" / "trace_requests.jsonl"
TRACES = ROOT / "evals" / "traces.csv"
COLUMNS = [
    "id", "category", "pass_fail", "note", "merchant", "message", "answer", "status",
    "escalation", "plan", "steps", "sources", "earlier_turns", "evidence", "cost_usd",
    "input_tokens", "output_tokens", "seconds",
]  # fmt: skip


def spent() -> tuple[float, float, float]:
    """Model spend and tokens so far in this process, from the Prometheus counters."""
    sample = REGISTRY.get_sample_value
    tokens = [
        sample("support_model_tokens_total", {"kind": kind}) or 0 for kind in ("input", "output")
    ]
    return (sample("support_model_cost_usd_total") or 0.0, *tokens)


def recorded(state: GraphState) -> dict[str, str]:
    """What the graph decided for the turn: the plan, each step's outcome, sources, evidence."""
    route = state.route
    plan = "; ".join(
        f"{step.agent.value}{' (added)' if step.requested_by else ''}: {step.query_pt}"
        for step in state.steps
    )
    return {
        "plan": f"{route.intent.value} {route.confidence:.2f}: {plan}" if route else "",
        "steps": "; ".join(
            f"{result.agent.value}: {result.refusal_reason or 'answered'}"
            for result in state.step_results
        ),
        "sources": "; ".join(s.url or f"{s.source_type.value}: {s.title}" for s in state.sources),
        "evidence": mask_pii("\n---\n".join(state.evidence))[:1500],
    }


async def trace(http: httpx.AsyncClient, graph, row: dict) -> dict:
    """Run a request's turns as its merchant and record the last one."""
    headers = {"Authorization": f"Bearer {token('merchant', row['merchant'], 3600)}"}
    earlier = []
    for message in row["turns"]:
        before, started = spent(), time.perf_counter()
        body = {"message": message, "conversation_id": row["id"]}
        response = await http.post("/chat", json=body, headers=headers)
        seconds, after = time.perf_counter() - started, spent()
        reply = response.json() if response.status_code == 200 else {}
        answer = reply.get("answer") or f"HTTP {response.status_code}: {response.text[:300]}"
        earlier.append(f"merchant: {mask_pii(message)}\nassistant: {mask_pii(answer)}")
    config = {"configurable": {"thread_id": thread_id(row["merchant"], row["id"])}}
    state = GraphState.model_validate(
        (await graph.aget_state(config)).values or {"message": "", "user_id": ""}
    )
    escalation = (reply.get("escalation") or {}).get("reason", "")
    return {
        "id": row["id"], "category": row["category"], "pass_fail": "", "note": "",
        "merchant": row["merchant"], "message": mask_pii(row["turns"][-1]),
        "answer": mask_pii(answer),
        "status": reply.get("status", f"HTTP {response.status_code}"), "escalation": escalation,
        **recorded(state), "earlier_turns": "\n\n".join(earlier[:-1]),
        "cost_usd": f"{after[0] - before[0]:.6f}", "input_tokens": int(after[1] - before[1]),
        "output_tokens": int(after[2] - before[2]), "seconds": f"{seconds:.1f}",
    }  # fmt: skip


async def main(limit: int | None) -> None:
    os.chdir(ROOT)
    keys()
    os.environ.setdefault("AUTH_JWKS_URL", str(PUBLIC))
    os.environ.update(STATE_DIR=tempfile.mkdtemp(prefix="traces-"), RATE_LIMIT_PER_MINUTE="1000")
    rows = [json.loads(line) for line in REQUESTS.read_text(encoding="utf-8").splitlines()]
    app = create_app()
    async with app.router.lifespan_context(app):
        configure_logging(logging.WARNING)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://traces", timeout=300
        ) as http:
            traces = []
            for row in rows[:limit]:
                traces.append(await trace(http, app.state.graph, row))
                print(
                    f"{row['id']} {traces[-1]['status']} {traces[-1]['escalation']}",
                    file=sys.stderr,
                )
    with TRACES.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(traces)
    print(f"wrote {len(traces)} traces to {TRACES.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, help="only the first N requests, for a smoke run")
    asyncio.run(main(parser.parse_args().limit))
