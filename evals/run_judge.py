"""LLM-as-judge: the challenge scenarios, two follow-ups, a compound request and the human-review
loop through the real graph, every answered turn scored for groundedness and relevance.

    uv run python -m evals.run_judge [--runs 3]

Calls the real chat model, the embedder, the web search and the judge (`JUDGE_MODEL`), so it
needs OPENAI_API_KEY and TAVILY_API_KEY and costs cents. The judge reads exactly what the system
had in hand for that turn, its `evidence`, so a fluent answer the evidence does not back scores
low. Every line runs three times by default, and `evals/report.md` reports each line's mean and
minimum and the spread between runs, never one chosen run. Like every eval, it never runs inside
pytest.
"""

import argparse
import asyncio
import json
import statistics
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from uuid import uuid4

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.checkpoints import checkpoint_serializer
from app.contracts import ChatResponse, Intent, TargetAgent
from app.graph.build import build_graph
from app.graph.memory import thread_id, turn_input
from app.graph.state import GraphState
from app.main import merchant_today
from app.prompts import load_prompt
from app.providers.llm import OpenAIProvider, build_provider
from app.rag.retrieve import HybridRetriever
from app.responses import to_response
from app.settings import Settings, get_settings
from app.tools.web import WebSearch
from evals.run_routing_eval import load_rows

REPORT = Path(__file__).parent / "report.md"
CALIBRATION = Path(__file__).parent / "retrieval_calibration.md"
CALLER = "cliente1988"
JUDGE_EFFORT = "medium"
RUNS = 3
"""One run is a reading, not a measurement: web results and sampling move between runs."""
OPERATOR_REPLY = (
    "Verificamos: o estorno da venda de R$ 89,50 no Mastercard final 7788 foi aprovado hoje, e o "
    "valor volta ao comprador em até 2 dias úteis."
)


class Verdict(BaseModel):
    """The judge's scores for one answered turn."""

    groundedness: int = Field(ge=1, le=5)
    relevance: int = Field(ge=1, le=5)
    rationale: str


@dataclass(frozen=True)
class Line:
    """A conversation to run, and the operator's reply if one of its turns pauses for a human."""

    name: str
    turns: list[str]
    operator: str | None = None


@dataclass
class Turn:
    """One merchant turn as it was answered, and what the judge made of it."""

    line: str
    label: str
    question: str
    conversation: list[dict]
    evidence: list[str]
    response: ChatResponse
    retrieval: float | None = None
    verdict: Verdict | None = field(default=None)


def lines() -> list[Line]:
    """The ten challenge scenarios, then the multi-turn, compound and human-review lines."""
    challenge = [row for row in load_rows() if row["group"] == "challenge"]
    return [
        *(Line(row["id"], [row["message"]]) for row in challenge),
        Line("FOLLOWUP-A", ["Quando cai o dinheiro das vendas de ontem?", "E o de amanhã?"]),
        Line(
            "FOLLOWUP-B", ["Como funciona o link de pagamento?", "E dá pra mandar pelo WhatsApp?"]
        ),
        Line("COMPOUND", ["Quando cai o dinheiro de ontem e como funciona a antecipação?"]),
        Line(
            "HITL",
            [
                "Por que o estorno da minha venda no Mastercard final 7788 ainda não apareceu?",
                "E em que dia foi feita essa venda?",
            ],
            operator=OPERATOR_REPLY,
        ),
    ]


def corpus_confidence(final: dict, retriever: HybridRetriever) -> float | None:
    """The retrieval confidence of the turn's corpus step, if it had one."""
    state = GraphState.model_validate(final)
    if state.route is None or state.route.intent is Intent.GENERAL_INFO:
        return None
    steps = [step for step in state.route.steps if step.agent is TargetAgent.KNOWLEDGE]
    if not steps:
        return None
    return retriever.search(steps[0].query_pt, steps[0].search_queries).confidence


async def run_line(graph, retriever: HybridRetriever, line: Line) -> list[Turn]:
    """Send every turn of a line in one conversation, resuming a pause with the operator."""
    conversation_id = f"judge-{uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id(CALLER, conversation_id)}}
    history: list[dict] = []
    turns: list[Turn] = []
    for number, message in enumerate(line.turns, start=1):
        turn = turn_input(
            message=message,
            user_id=CALLER,
            conversation_id=conversation_id,
            trace_id=uuid4().hex,
            today=merchant_today(),
        )
        final = await graph.ainvoke(turn, config)
        response = to_response(final, conversation_id)
        label = f"turn {number}" if len(line.turns) > 1 else ""
        evidence = list(final.get("evidence") or [])
        confidence = corpus_confidence(final, retriever)
        turns.append(Turn(line.name, label, message, list(history), evidence, response, confidence))
        history += [
            {"role": "human", "content": message},
            {"role": "ai", "content": response.answer},
        ]
        if final.get("__interrupt__") and line.operator:
            decision = {"action": "reply", "answer": line.operator}
            resumed = to_response(
                await graph.ainvoke(Command(resume=decision), config), conversation_id
            )
            turns.append(
                Turn(line.name, "operator reply", message, list(history), [line.operator], resumed)
            )
            history.append({"role": "ai", "content": resumed.answer})
    return turns


async def judge(turn: Turn, provider: OpenAIProvider) -> Verdict:
    """Score one answered turn against the evidence it had."""
    payload = {
        "conversation": turn.conversation,
        "question": turn.question,
        "evidence": turn.evidence,
        "answer": turn.response.answer,
    }
    return await provider.structured(
        system=load_prompt("judge").text,
        user=json.dumps(payload, ensure_ascii=False),
        schema=Verdict,
    )


def scored(turn: Turn) -> bool:
    """An answered turn is judged; a pause for a human is a handoff notice, listed instead."""
    return turn.response.status == "answered"


def revision() -> str:
    """The commit the run measured, flagged when the working tree held more than that commit."""
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, check=False
    ).stdout.strip()
    return f"`{sha}`" + (" plus the uncommitted changes that add this runner" if dirty else "")


def calibration_summary() -> list[str]:
    """The summary table of the committed retrieval calibration report."""
    table, started = [], False
    for row in CALIBRATION.read_text(encoding="utf-8").splitlines():
        if row.startswith("| Embedder"):
            started = True
        if started and not row.startswith("|"):
            break
        if started:
            table.append(row)
    return table


Key = tuple[str, str]


def grouped(runs: list[list[Turn]]) -> dict[Key, list[Turn]]:
    """Every turn of every run, under its line and turn, in the order the lines run."""
    groups: dict[Key, list[Turn]] = {}
    for turns in runs:
        for turn in turns:
            groups.setdefault((turn.line, turn.label), []).append(turn)
    return groups


def outcome(turn: Turn) -> str:
    """How one run of a turn ended, in the report's words."""
    escalation = turn.response.escalation
    if turn.response.status == "pending_human":
        return f"escalated ({escalation.reason.value})"
    return "answered by the operator" if escalation else "answered"


def spread(values: list[int]) -> str:
    """Mean and minimum of one score over the runs that scored it."""
    return f"{statistics.mean(values):.2f} / {min(values)}" if values else "—"


def line_rows(groups: dict[Key, list[Turn]], runs: int) -> list[str]:
    """One row per line and turn: how it ended in each run, and its scores across them."""
    rows = [
        f"| Line | Turn | Outcome over {runs} runs | Groundedness mean / min "
        "| Relevance mean / min | Note from its lowest-scored run |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for (line, label), turns in groups.items():
        outcomes = Counter(outcome(turn) for turn in turns)
        ended = ", ".join(f"{name} {count}/{runs}" for name, count in outcomes.most_common())
        judged = [turn for turn in turns if turn.verdict]
        worst = min(
            judged, key=lambda t: t.verdict.groundedness + t.verdict.relevance, default=None
        )
        rows.append(
            f"| {line} | {label} | {ended} "
            f"| {spread([t.verdict.groundedness for t in judged])} "
            f"| {spread([t.verdict.relevance for t in judged])} "
            f"| {worst.verdict.rationale if worst else 'not scored: a handoff notice'} |"
        )
    return rows


def run_rows(runs: list[list[Turn]]) -> list[str]:
    """One row per run, then the spread of the run means."""
    rows = [
        "| Run | Scored turns | Escalated turns | Groundedness | Relevance |",
        "| --- | --- | --- | --- | --- |",
    ]
    means: list[tuple[float, float]] = []
    for number, turns in enumerate(runs, start=1):
        judged = [turn.verdict for turn in turns if turn.verdict]
        escalated = sum(turn.response.status == "pending_human" for turn in turns)
        mean = (
            statistics.mean(v.groundedness for v in judged),
            statistics.mean(v.relevance for v in judged),
        )
        means.append(mean)
        rows.append(f"| {number} | {len(judged)} | {escalated} | {mean[0]:.2f} | {mean[1]:.2f} |")
    grounded, relevant = zip(*means, strict=True)
    rows += [
        "",
        f"Across runs: groundedness {statistics.mean(grounded):.2f} ± "
        f"{statistics.stdev(grounded):.2f} (range {min(grounded):.2f} to {max(grounded):.2f}), "
        f"relevance {statistics.mean(relevant):.2f} ± {statistics.stdev(relevant):.2f} "
        f"(range {min(relevant):.2f} to {max(relevant):.2f}), mean ± standard deviation of the "
        "run means.",
    ]
    return rows


def lowest(groups: dict[Key, list[Turn]]) -> list[str]:
    """Every scored run below 4 on either score, with the answer the judge read."""
    out = ["## Every score below 4", ""]
    for (line, label), turns in groups.items():
        for run, turn in enumerate(turns, start=1):
            verdict = turn.verdict
            if verdict is None or min(verdict.groundedness, verdict.relevance) >= 4:
                continue
            out += [
                f"**{f'{line} {label}'.strip()}, run {run}** — groundedness "
                f"{verdict.groundedness}, relevance "
                f"{verdict.relevance}. {verdict.rationale}",
                "",
                *(f"> {text}" for text in turn.response.answer.splitlines()),
                "",
            ]
    return out if len(out) > 2 else [*out, "None.", ""]


def render(runs: list[list[Turn]], settings: Settings) -> str:
    """The committed report: every run, aggregated, none chosen."""
    groups = grouped(runs)
    s09 = [turn.retrieval for turn in groups[("S09", "")] if turn.retrieval is not None]
    report = [
        "# LLM-as-judge report",
        "",
        f"Written by `uv run python -m evals.run_judge` on {date.today().isoformat()}, at "
        f"{revision()}: {len(runs)} independent runs of every line, aggregated, no run chosen. "
        f"Chat model `{settings.llm_model}` at {settings.llm_reasoning_effort} effort; embedder "
        f"`{settings.embedding_model}` with a retrieval threshold of "
        f"{settings.retrieval_confidence_threshold}; judge `{settings.judge_model}` at "
        f"{JUDGE_EFFORT} effort. Every answered turn is scored 1 to 5 for groundedness (each "
        "claim is in the evidence the system had for that turn: excerpts, account data, web pages "
        "or the operator's reply) and relevance (it answers what was asked). A turn that paused "
        "for a human is counted, not scored: its answer is a fixed handoff notice. Web answers "
        "use live search results, so they change from run to run.",
        "",
        *line_rows(groups, len(runs)),
        "",
        "## Run to run",
        "",
        *run_rows(runs),
        "",
        "## Retrieval threshold",
        "",
        f"S09 (crediário) retrieved at {', '.join(f'{value:.3f}' for value in s09)} across the "
        f"runs, against a threshold of {settings.retrieval_confidence_threshold} that comes from "
        "`evals/retrieval_calibration.md`:",
        "",
        *calibration_summary(),
        "",
        *lowest(groups),
    ]
    return "\n".join(report)


async def run_once(settings: Settings, retriever: HybridRetriever) -> list[Turn]:
    """Every line through a fresh graph, then every answered turn judged."""
    judge_provider = OpenAIProvider(
        api_key=settings.openai_api_key, model=settings.judge_model, reasoning_effort=JUDGE_EFFORT
    )
    async with httpx.AsyncClient() as client:
        graph = build_graph(
            build_provider(settings),
            settings,
            retriever,
            InMemorySaver(serde=checkpoint_serializer()),
            None,
            WebSearch(settings, client),
        )
        turns = [turn for line in lines() for turn in await run_line(graph, retriever, line)]
    for turn in turns:
        if scored(turn):
            turn.verdict = await judge(turn, judge_provider)
    return turns


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=RUNS, help="independent runs to aggregate")
    arguments = parser.parse_args()
    settings = get_settings()
    retriever = HybridRetriever.from_settings(settings)
    runs = [await run_once(settings, retriever) for _ in range(arguments.runs)]
    REPORT.write_text(render(runs, settings), encoding="utf-8")
    print(REPORT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    asyncio.run(main())
