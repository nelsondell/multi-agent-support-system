"""Route-specific extraction from the raw corpus.

Two routes, both confirmed against files in `data/raw/` before this module was written:

- FAQ (`/duvidas/`): questions are `a.c-all-faq__question`, answers live in separate
  `div.o-modal__content--faq` nodes, and the two are paired by their `data-modal` id. The answer
  node repeats the question as its first line, so it is stripped.
- Article (Beaver Builder): body text is under `.fl-col-content`. Generic extraction returns
  mostly footer on these pages, which is why the selector is explicit.

Meta descriptions are ignored everywhere: they repeat verbatim across pages as boilerplate.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from bs4 import BeautifulSoup

_MIN_ARTICLE_CHARS = 400
_WHITESPACE = re.compile(r"\s+")


class Route(StrEnum):
    """How a page has to be read."""

    FAQ = "faq"
    ARTICLE = "article"


@dataclass(frozen=True)
class Document:
    """One extracted unit of knowledge, before chunking."""

    url: str
    title: str
    section: str
    text: str
    route: Route


def _clean(text: str) -> str:
    """Collapse the whitespace that the page templates leave behind."""
    return _WHITESPACE.sub(" ", text).strip()


def _page_title(soup: BeautifulSoup) -> str:
    """Prefer the document title, falling back to the first heading."""
    if soup.title and soup.title.get_text(strip=True):
        return _clean(soup.title.get_text())
    heading = soup.find(["h1", "h2"])
    return _clean(heading.get_text()) if heading else ""


def detect_route(soup: BeautifulSoup) -> Route | None:
    """Pick the parser for a page, or None when neither route applies."""
    if soup.select("a.c-all-faq__question"):
        return Route.FAQ
    body = "".join(node.get_text(" ", strip=True) for node in soup.select(".fl-col-content"))
    return Route.ARTICLE if len(body) >= _MIN_ARTICLE_CHARS else None


def parse_faq(soup: BeautifulSoup, url: str) -> list[Document]:
    """Pair every question with its answer modal through the shared `data-modal` id."""
    title = _page_title(soup)
    answers = {
        node.get("data-modal"): _clean(node.get_text(" ", strip=True))
        for node in soup.select("div.o-modal__content--faq")
    }
    documents = []
    for anchor in soup.select("a.c-all-faq__question"):
        question = _clean(anchor.get_text(" ", strip=True))
        answer = answers.get(anchor.get("data-modal"), "")
        if not question or not answer:
            continue
        if answer.startswith(question):
            answer = answer[len(question) :].strip()
        if not answer:
            continue
        documents.append(
            Document(
                url=url,
                title=title,
                section=_faq_category(anchor),
                text=f"{question}\n{answer}",
                route=Route.FAQ,
            )
        )
    return documents


def _faq_category(anchor) -> str:
    """The nearest preceding category heading, used as the section label."""
    block = anchor.find_parent(class_="c-all-faq__content")
    heading = block.find_previous("h2", class_="c-all-faq__title") if block else None
    return _clean(heading.get_text()) if heading else ""


def parse_article(soup: BeautifulSoup, url: str) -> list[Document]:
    """Take the Beaver Builder column content as the page body."""
    title = _page_title(soup)
    parts = [_clean(node.get_text(" ", strip=True)) for node in soup.select(".fl-col-content")]
    text = "\n".join(part for part in parts if part)
    if len(text) < _MIN_ARTICLE_CHARS:
        return []
    return [Document(url=url, title=title, section="", text=text, route=Route.ARTICLE)]


def parse_page(html: str, url: str) -> list[Document]:
    """Extract a page through whichever route it matches."""
    soup = BeautifulSoup(html, "html.parser")
    route = detect_route(soup)
    if route is Route.FAQ:
        return parse_faq(soup, url)
    if route is Route.ARTICLE:
        return parse_article(soup, url)
    return []
