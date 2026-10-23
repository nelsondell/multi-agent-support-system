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
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.observability import mask_pii
from app.observability.metrics import VENDOR_FAILURES
from app.observability.otel import operation
from app.settings import MERCHANT_TIMEZONE, Settings

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"

_WITH_DIGIT = re.compile(r"\S*\d\S*")
_DAY_MONTH_YEAR = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_YEAR_MONTH_DAY = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_WRITTEN = re.compile(r"\b(\d{1,2}) de ([a-zç]{3})[a-zç]*\.? de (\d{4})\b", re.IGNORECASE)
_MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
_SPACES = re.compile(r"\s+")
_EDGES = ".,;:!?()[]{}\"'"


class WebSearchUnavailable(RuntimeError):
    """The search could not be made or returned nothing usable."""


class NoWebResults(WebSearchUnavailable):
    """The search worked and found nothing usable."""


@dataclass(frozen=True)
class WebResult:
    """One page returned by the search, as untrusted data."""

    url: str
    title: str
    content: str
    published: str | None = None
    """When the provider knows it, the page's publication date, as the provider writes it."""

    def stated_days(self) -> list[date]:
        """The calendar days the page's own text names: untrusted, and never a publication date."""
        text = f"{self.title} {self.content}"
        found = [(int(d), int(m), int(y)) for d, m, y in _DAY_MONTH_YEAR.findall(text)]
        found += [(int(d), int(m), int(y)) for y, m, d in _YEAR_MONTH_DAY.findall(text)]
        found += [
            (int(d), _MONTHS.index(m.lower()) + 1, int(y))
            for d, m, y in _WRITTEN.findall(text)
            if m.lower() in _MONTHS
        ]
        days = []
        for day, month, year in found:
            try:
                days.append(date(year, month, day))
            except ValueError:
                continue  # 10/14/2024 is a month-first date, not one this site would write
        return days

    @property
    def published_on(self) -> date | None:
        """The merchant's calendar day the page was published, from the provider's structured
        field only; None when the provider gave none or wrote one that cannot be read."""
        if not self.published:
            return None
        try:
            moment = parsedate_to_datetime(self.published)
        except (TypeError, ValueError):
            try:
                moment = datetime.fromisoformat(self.published)
            except ValueError:
                return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(MERCHANT_TIMEZONE).date()


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

    async def search(self, query: str, recent: bool = False) -> list[WebResult]:
        """Up to `web_search_max_results` pages for an already-sanitised query; with `recent`,
        only pages from the last day."""
        if not self._key:
            raise WebSearchUnavailable("TAVILY_API_KEY is not set")
        with operation("execute_tool", "web_search", **{"gen_ai.tool.name": "web_search"}):
            try:
                return await self._search(query, recent)
            except NoWebResults:
                raise  # the vendor answered; there was just nothing to use
            except WebSearchUnavailable as exc:
                kind = "timeout" if isinstance(exc.__cause__, httpx.TimeoutException) else "error"
                VENDOR_FAILURES.labels(vendor="web", kind=kind).inc()
                raise

    async def _search(self, query: str, recent: bool) -> list[WebResult]:
        body = {"query": query, "max_results": self._max_results, "search_depth": "basic"}
        if recent:
            body["time_range"] = "day"
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
                    published=row.get("published_date"),
                )
            )
    if not usable:
        raise NoWebResults("web search returned no usable result")
    return usable
