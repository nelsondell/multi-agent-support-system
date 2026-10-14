"""LLM-as-judge: the challenge scenarios, two follow-ups, a compound request and the human-review
loop through the real graph, every answered turn scored for groundedness and relevance.

    uv run python -m evals.run_judge

Calls the real chat model, the embedder, the web search and the judge (`JUDGE_MODEL`), so it
needs OPENAI_API_KEY and TAVILY_API_KEY and costs cents. The judge reads exactly what the system
had in hand for that turn, its `evidence`, so a fluent answer the evidence does not back scores
low. Writes `evals/report.md`. Like every eval, it never runs inside pytest.
"""

import asyncio
import json
import statistics
import subprocess
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


def score_rows(turns: list[Turn]) -> list[str]:
    """One table row per turn, scored or not."""
    rows = [
        "| Line | Turn | Status | Agents | Retrieval | Groundedness | Relevance | Judge's note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for turn in turns:
        response = turn.response
        retrieval = f"{turn.retrieval:.3f}" if turn.retrieval is not None else "—"
        agents = ", ".join(agent.value for agent in response.agents) or "—"
        status = response.status
        if response.escalation:
            status += f" ({response.escalation.reason.value})"
        if turn.verdict is None:
            rows.append(
                f"| {turn.line} | {turn.label} | {status} | {agents} | {retrieval} | — | — "
                "| not scored: a handoff notice |"
            )
            continue
        verdict = turn.verdict
        rows.append(
            f"| {turn.line} | {turn.label} | {status} | {agents} | {retrieval} "
            f"| {verdict.groundedness} | {verdict.relevance} | {verdict.rationale} |"
        )
    return rows


def transcript(turns: list[Turn]) -> list[str]:
    """The questions and answers behind the scores, so a reader can check the judge."""
    out = ["## Answers", ""]
    for turn in turns:
        title = f"{turn.line} {turn.label}".strip()
        sources = ", ".join(source.url or source.title for source in turn.response.sources)
        out += [
            f"**{title}** — {turn.question}",
            "",
            *(f"> {line}" for line in turn.response.answer.splitlines()),
            "",
            f"Sources: {sources or 'none'}. Evidence items: {len(turn.evidence)}.",
            "",
        ]
    return out


def render(turns: list[Turn], settings: Settings) -> str:
    """The committed report."""
    judged = [turn.verdict for turn in turns if turn.verdict]
    s09 = next(turn.retrieval for turn in turns if turn.line == "S09")
    lines_out = [
        "# LLM-as-judge report",
        "",
        f"Written by `uv run python -m evals.run_judge` on {date.today().isoformat()}, at "
        f"{revision()}. Chat model `{settings.llm_model}` at {settings.llm_reasoning_effort} "
        f"effort; embedder `{settings.embedding_model}` with a retrieval threshold of "
        f"{settings.retrieval_confidence_threshold}; judge `{settings.judge_model}` at "
        f"{JUDGE_EFFORT} effort. Every answered turn is scored 1 to 5 for groundedness (each "
        "claim is in the evidence the system had for that turn: excerpts, account data, web pages "
        "or the operator's reply) and relevance (it answers what was asked). A turn that paused "
        "for a human is listed, not scored: its answer is a fixed handoff notice.",
        "",
        *score_rows(turns),
        "",
        f"**Means over {len(judged)} scored turns:** groundedness "
        f"{statistics.mean(v.groundedness for v in judged):.2f}, relevance "
        f"{statistics.mean(v.relevance for v in judged):.2f}.",
        "",
        "## Retrieval threshold",
        "",
        f"S09 (crediário) retrieved with a confidence of {s09:.3f} against the "
        f"{settings.retrieval_confidence_threshold} threshold in this run. The threshold comes "
        "from `evals/retrieval_calibration.md`:",
        "",
        *calibration_summary(),
        "",
        *transcript(turns),
    ]
    return "\n".join(lines_out)


async def main() -> None:
    settings = get_settings()
    retriever = HybridRetriever.from_settings(settings)
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
    REPORT.write_text(render(turns, settings), encoding="utf-8")
    print(REPORT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    asyncio.run(main())
