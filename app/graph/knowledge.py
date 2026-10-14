"""The knowledge agent: answer from the corpus or, for general-purpose questions, from the web.

For anything about the acquirer there is no third option: without retrieved text above the
confidence threshold, or when the model reports that the retrieved text does not answer the
question, the agent produces nothing and the step goes to a human, because letting the model
answer from memory about rates and settlement dates is exactly the failure this system exists to
avoid, and "I found nothing" is not an answer. The web is
never that fallback. It answers only steps the router labelled `general_info`, such as the weather
or an exchange rate, and its pages are untrusted data: the answer may not carry a number they lack
or a link of its own, whatever a page tells the model to do.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from app.contracts import EscalationReason, Source, SourceType
from app.graph.numbers import adds_numbers
from app.prompts import load_prompt
from app.providers.llm import LLMProvider, LLMProviderError
from app.rag.retrieve import Hit, HybridRetriever, RetrievalResult, RetrievalUnavailable
from app.tools.web import (
    NoWebResults,
    WebResult,
    WebSearch,
    WebSearchUnavailable,
    outbound_query,
)

if TYPE_CHECKING:
    from app.graph.state import GraphState

_LINK = re.compile(r"https?://|www\.|[\w.+-]+@[\w-]+\.\w", re.IGNORECASE)

logger = logging.getLogger(__name__)


Coverage = Literal["full", "partial", "none"]


class KnowledgeAnswer(BaseModel):
    """The grounded answer, with the pages it was built from."""

    coverage: Coverage
    """How much of the question the excerpts or results answer. `full` and `partial` are
    answered, a partial one saying plainly what the site does not cover; with `none` the text is
    discarded: a corpus step abstains and a web step degrades, instead of answering that nothing
    was found."""
    answer: str
    used_urls: list[str] = Field(default_factory=list)
    data_date: str | None = None
    """Web answers about a given day only: the day the data refers to, as the source shows it,
    as AAAA-MM-DD; null when the source shows no date."""


def format_context(hits: list[Hit]) -> str:
    """Lay the retrieved chunks out for the prompt, each labelled with its url."""
    return "\n\n".join(
        f"[trecho {position}] url: {hit.chunk.url}\ntítulo: {hit.chunk.title}\n{hit.chunk.text}"
        for position, hit in enumerate(hits, start=1)
    )


def _sources(hits: list[Hit], used_urls: list[str]) -> list[Source]:
    """Cite the retrieved pages the answer drew on, deduplicated and never invented."""
    retrieved = {hit.chunk.url: hit.chunk.title for hit in hits}
    urls = [url for url in used_urls if url in retrieved] or list(retrieved)
    seen: set[str] = set()
    return [
        Source(url=url, title=retrieved[url]) for url in urls if not (url in seen or seen.add(url))
    ]


def _refusal(reason: EscalationReason, looked_at: dict[str, Any]) -> dict[str, Any]:
    """Abstain: no generated text, an explicit reason, and a handoff. What was retrieved still
    goes to the operator, as what was looked at."""
    return {
        "answer": load_prompt("no_knowledge").text,
        "refusal_reason": reason.value,
        "sources": [],
        **looked_at,
    }


def retrieve(state: GraphState, retriever: HybridRetriever) -> RetrievalResult:
    """Search the corpus for the step's question and its per-item searches.

    An unreachable embedder reads as nothing retrieved, so the step abstains instead of raising.
    """
    step = state.current_step()
    try:
        return retriever.search(state.current_query(), step.search_queries if step else ())
    except RetrievalUnavailable as exc:
        logger.warning("retrieval unavailable, abstaining: %s", exc)
        return RetrievalResult(hits=[], confidence=0.0)


async def answer(
    state: GraphState, provider: LLMProvider, result: RetrievalResult, threshold: float
) -> dict[str, Any]:
    """Answer the question from retrieved sources, or refuse below the threshold."""
    looked_at = {
        "consulted": _sources(result.hits, []),
        "evidence": [*state.evidence, *(hit.chunk.text for hit in result.hits)],
    }
    if not result.hits or result.confidence < threshold:
        logger.info(
            "knowledge abstained: confidence=%.3f threshold=%.3f hits=%d",
            result.confidence,
            threshold,
            len(result.hits),
        )
        return _refusal(EscalationReason.LOW_RETRIEVAL_CONFIDENCE, looked_at)

    prompt = load_prompt("knowledge")
    question = state.current_query()
    try:
        generated = await provider.structured(
            system=prompt.text,
            user=f"Pergunta do cliente:\n{question}\n\nTrechos:\n{format_context(result.hits)}",
            schema=KnowledgeAnswer,
        )
    except LLMProviderError as exc:
        logger.warning("knowledge generation failed: %s", exc)
        return _refusal(EscalationReason.LOW_RETRIEVAL_CONFIDENCE, looked_at)
    if generated.coverage == "none":
        logger.info("knowledge abstained: the excerpts do not answer the question")
        return _refusal(EscalationReason.INSUFFICIENT_SOURCES, looked_at)

    return {
        "answer": generated.answer,
        "sources": _sources(result.hits, generated.used_urls),
        **looked_at,
    }


def _web_sources(results: list[WebResult], used_urls: list[str]) -> list[Source]:
    """Cite the result pages the answer drew on, never a url the model made up."""
    by_url = {result.url: result.title for result in results}
    urls = list(dict.fromkeys(url for url in used_urls if url in by_url)) or list(by_url)
    return [Source(source_type=SourceType.WEB, url=url, title=by_url[url]) for url in urls]


def _web_unavailable(looked_at: dict[str, Any] | None = None) -> dict[str, Any]:
    """The degraded answer: a pt-BR message, no sources, and no escalation."""
    return {"answer": load_prompt("web_unavailable").text, "sources": [], **(looked_at or {})}


def trustworthy(answer: str, results: list[WebResult], state: GraphState) -> bool:
    """Hard rule 4 for the web: every number comes from a result (or is today's date, or the day
    asked about), and the answer carries no link or e-mail of its own, which is how a planted
    instruction would show."""
    allowed = [f"{result.title} {result.content} {result.published or ''}" for result in results]
    allowed += [day.isoformat() for day in (state.today, target_date(state)) if day is not None]
    return bool(answer.strip()) and not adds_numbers(answer, allowed) and not _LINK.search(answer)


def shown_in(day: date, results: list[WebResult]) -> bool:
    """Whether the results write this day in one of the usual forms."""
    text = " ".join(f"{r.title} {r.content} {r.published or ''}" for r in results).lower()
    forms = {
        f"{day:%d/%m}",
        f"{day.day}/{day.month}",
        day.isoformat(),
        f"{day.day} de {PT_MONTHS[day.month - 1]}",
        f"{day:%d %b %Y}".lower(),
    }
    return any(form in text for form in forms)


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def dated(generated: KnowledgeAnswer, results: list[WebResult], target: date | None) -> str:
    """The answer, followed by a plain notice when its data is not shown to be from the day asked
    about. A date the model reports is printed only if the results show it."""
    if target is None:
        return generated.answer
    day = _day(generated.data_date)
    if day == target and shown_in(target, results):
        return generated.answer
    asked = f"{target:%d/%m/%Y}"
    if day is not None and day != target and shown_in(day, results):
        notice = load_prompt("web_other_day").text.format(data_date=f"{day:%d/%m/%Y}", asked=asked)
    else:
        notice = load_prompt("web_undated").text.format(asked=asked)
    return f"{generated.answer}\n\n{notice}"


DAY_OFFSET = {"yesterday": -1, "today": 0, "tomorrow": 1}
PT_MONTHS = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)  # fmt: skip


def target_date(state: GraphState) -> date | None:
    """The day a general-information step asks about, when its answer depends on one."""
    step = state.current_step()
    if step is None or step.target_day is None or state.today is None:
        return None
    return state.today + timedelta(days=DAY_OFFSET[step.target_day])


def web_query(state: GraphState) -> str:
    """The masked query that leaves the process, carrying the date of the day it asks about.

    A search engine cannot resolve "amanhã", and today's date pulls today's pages, so a forecast
    for tomorrow is searched with tomorrow's date. The date is added after masking: it is the
    system's own, never something the merchant typed.
    """
    query = outbound_query(state.current_query(), state.message)
    day = target_date(state)
    return f"{query} {day:%d/%m/%Y}" if day is not None else query


async def search_recent_first(web: WebSearch, query: str, recent: bool) -> list[WebResult]:
    """For a question about a given day, pages from the last day first, then any page."""
    if recent:
        try:
            return await web.search(query, recent=True)
        except NoWebResults:
            logger.info("no page from the last day; searching without the recency filter")
    return await web.search(query)


async def answer_from_web(
    state: GraphState, provider: LLMProvider, web: WebSearch | None
) -> dict[str, Any]:
    """Answer a general-purpose step from a cited web search, or say it cannot be looked up."""
    question = state.current_query()
    target = target_date(state)
    try:
        if web is None:
            raise WebSearchUnavailable("web search is not configured")
        results = await search_recent_first(web, web_query(state), target is not None)
    except WebSearchUnavailable as exc:
        logger.warning("web search unavailable: %s", exc)
        return _web_unavailable()
    looked_at = {
        "consulted": _web_sources(results, []),
        "evidence": [*state.evidence, *(result.content for result in results)],
    }
    payload = {
        "question": question,
        "today": state.today.isoformat() if state.today else None,
        "target_date": target.isoformat() if target else None,
        "results": [asdict(result) for result in results],
    }
    try:
        generated = await provider.structured(
            system=load_prompt("knowledge_web").text,
            user=json.dumps(payload, ensure_ascii=False),
            schema=KnowledgeAnswer,
        )
    except LLMProviderError as exc:
        logger.warning("web answer generation failed: %s", exc)
        return _web_unavailable(looked_at)
    if generated.coverage == "none":
        logger.info("web answer withheld: the results do not answer the question")
        return _web_unavailable(looked_at)
    if not trustworthy(generated.answer, results, state):
        logger.warning("web answer rejected: a number or a link not found in the results")
        return _web_unavailable(looked_at)
    sources = _web_sources(results, generated.used_urls)
    return {"answer": dated(generated, results, target), "sources": sources, **looked_at}
