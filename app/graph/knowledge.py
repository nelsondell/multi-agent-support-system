"""The knowledge agent: answer from the corpus, or refuse and hand over.

There is no third option. Without retrieved text above the confidence threshold the agent
produces nothing, because the alternative — letting the model answer from memory about rates and
settlement dates — is exactly the failure this system exists to avoid.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from app.contracts import EscalationReason, Source
from app.prompts import load_prompt
from app.providers.llm import LLMProvider, LLMProviderError
from app.rag.retrieve import Hit, RetrievalResult

if TYPE_CHECKING:
    from app.graph.state import GraphState

logger = logging.getLogger(__name__)


class KnowledgeAnswer(BaseModel):
    """The grounded answer, with the pages it was built from."""

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

    return {
        "answer": generated.answer,
        "sources": _sources(result.hits, generated.used_urls),
        **looked_at,
    }
