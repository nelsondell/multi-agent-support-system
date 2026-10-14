"""Web search, for general-purpose questions only: the weather, an exchange rate.

The knowledge agent calls it for steps the router labelled `general_info`, never as a fallback for
a product question the corpus cannot answer, and the support agent cannot call it at all. What
leaves the process is the step's standalone question, PII-masked, with every token carrying a
digit removed unless the merchant typed it in this very message, so an amount or an id read from
the account or the history can never become part of a web query.

Every failure (no key, a timeout, an error status, an unreadable body) raises
`WebSearchUnavailable`, which the agent turns into a pt-BR message instead of an exception.
"""

import logging
import re
from dataclasses import dataclass

import httpx

from app.observability import mask_pii
from app.settings import Settings

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"

_WITH_DIGIT = re.compile(r"\S*\d\S*")
_SPACES = re.compile(r"\s+")
_EDGES = ".,;:!?()[]{}\"'"


class WebSearchUnavailable(RuntimeError):
    """The search could not be made or returned nothing usable."""


@dataclass(frozen=True)
class WebResult:
    """One page returned by the search, as untrusted data."""

    url: str
    title: str
    content: str


def outbound_query(query: str, message: str) -> str:
    """The text that may leave the process: masked, and with no number or id the merchant did not
    write in the current message."""
    typed = {token.strip(_EDGES) for token in _WITH_DIGIT.findall(mask_pii(message))}
    masked = mask_pii(query)
    kept = _WITH_DIGIT.sub(
        lambda token: token[0] if token[0].strip(_EDGES) in typed else "", masked
    )
    return _SPACES.sub(" ", kept).strip()


class WebSearch:
    """A Tavily search client: one POST per question, capped and with a timeout."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._key = settings.tavily_api_key
        self._max_results = settings.web_search_max_results
        self._timeout = settings.web_search_timeout_s
        self._client = client

    async def search(self, query: str) -> list[WebResult]:
        """Up to `web_search_max_results` pages for an already-sanitised query."""
        if not self._key:
            raise WebSearchUnavailable("TAVILY_API_KEY is not set")
        body = {"query": query, "max_results": self._max_results, "search_depth": "basic"}
        try:
            response = await self._client.post(
                TAVILY_SEARCH_URL,
                json=body,
                headers={"Authorization": f"Bearer {self._key}"},
                timeout=self._timeout,
            )
            response.raise_for_status()
            rows = response.json()["results"]
        except httpx.TimeoutException as exc:
            raise WebSearchUnavailable(f"web search timed out after {self._timeout}s") from exc
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise WebSearchUnavailable(f"web search failed: {exc}") from exc
        return _results(rows)[: self._max_results]


def _results(rows: list) -> list[WebResult]:
    """The usable rows: each needs a url and some text."""
    usable = []
    for row in rows:
        if isinstance(row, dict) and row.get("url") and (row.get("content") or row.get("title")):
            usable.append(
                WebResult(
                    url=row["url"],
                    title=row.get("title") or row["url"],
                    content=row.get("content") or "",
                )
            )
    if not usable:
        raise WebSearchUnavailable("web search returned no usable result")
    return usable
