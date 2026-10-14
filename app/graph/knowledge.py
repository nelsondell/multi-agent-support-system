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
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from app.contracts import EscalationReason, Source, SourceType
from app.graph.numbers import adds_numbers
from app.prompts import load_prompt
from app.providers.llm import LLMProvider, LLMProviderError
from app.rag.retrieve import Hit, HybridRetriever, RetrievalResult, RetrievalUnavailable
from app.tools.web import WebResult, WebSearch, WebSearchUnavailable, outbound_query

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
    """Hard rule 4 for the web: every number comes from a result (or is today's date), and the
    answer carries no link or e-mail of its own, which is how a planted instruction would show."""
    allowed = [f"{result.title} {result.content}" for result in results]
    if state.today is not None:
        allowed.append(state.today.isoformat())
    return bool(answer.strip()) and not adds_numbers(answer, allowed) and not _LINK.search(answer)


DAY_OFFSET = {"yesterday": -1, "today": 0, "tomorrow": 1}


def web_query(state: GraphState) -> str:
    """The masked query that leaves the process, carrying the date of the day it asks about.

    A search engine cannot resolve "amanhã", and today's date pulls today's pages, so a forecast
    for tomorrow is searched with tomorrow's date. The date is added after masking: it is the
    system's own, never something the merchant typed.
    """
    query = outbound_query(state.current_query(), state.message)
    step = state.current_step()
    if step is not None and step.target_day is not None and state.today is not None:
        day = state.today + timedelta(days=DAY_OFFSET[step.target_day])
        query = f"{query} {day:%d/%m/%Y}"
    return query


async def answer_from_web(
    state: GraphState, provider: LLMProvider, web: WebSearch | None
) -> dict[str, Any]:
    """Answer a general-purpose step from a cited web search, or say it cannot be looked up."""
    question = state.current_query()
    try:
        if web is None:
            raise WebSearchUnavailable("web search is not configured")
        results = await web.search(web_query(state))
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
    return {"answer": generated.answer, "sources": sources, **looked_at}
