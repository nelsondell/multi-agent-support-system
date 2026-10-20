"""Token-window chunking with hash de-duplication.

Chunks are 600 tokens with 100 of overlap, measured with the same tokenizer family the models
use, so a chunk that looks like it fits really does. De-duplication is by hash of the normalized
text: several routes on this site render the same body (the contact page repeats the home page,
the custom-rates page repeats the machine catalogue), and without this the index would count
those twice and rank them twice.

The tokenizer's merge table ships in `assets/`, so chunking never downloads it.
"""

import hashlib
import os
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import tiktoken

from app.rag.parse import Document

CHUNK_TOKENS = 600
CHUNK_OVERLAP = 100

_ENCODING = "cl100k_base"
ASSETS = Path(__file__).parent / "assets"
"""cl100k_base, under the name tiktoken caches it by: the SHA-1 of its download URL."""
_NORMALIZE = re.compile(r"[^0-9a-zà-ÿ]+")
_TOKEN = re.compile(r"[0-9a-zà-ÿ]+")


@dataclass(frozen=True)
class Chunk:
    """A retrievable unit, carrying everything needed to cite it."""

    id: str
    text: str
    url: str
    title: str
    section: str
    route: str


@dataclass(frozen=True)
class ChunkStats:
    """Counters for one chunking run."""

    generated: int
    discarded: int

    @property
    def total(self) -> int:
        """Chunks kept in the index."""
        return self.generated - self.discarded

    def summary(self) -> str:
        """One line, for the ingestion log."""
        return (
            f"chunks generated={self.generated} "
            f"discarded={self.discarded} (duplicates) total={self.total}"
        )


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, accents kept: they distinguish Portuguese terms."""
    return _TOKEN.findall(text.lower())


def text_hash(text: str) -> str:
    """Hash of the normalized text, used both as chunk id and as duplicate key."""
    normalized = _NORMALIZE.sub(" ", text.lower()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


@cache
def tokenizer() -> tiktoken.Encoding:
    """cl100k_base, read from the committed copy. tiktoken checks it against the encoding's
    SHA-256 and would download only on a mismatch; the cache directory points at the copy only for
    this load, so no other encoding is ever written next to it."""
    previous = os.environ.get("TIKTOKEN_CACHE_DIR")
    os.environ["TIKTOKEN_CACHE_DIR"] = str(ASSETS)
    try:
        return tiktoken.get_encoding(_ENCODING)
    finally:
        if previous is None:
            del os.environ["TIKTOKEN_CACHE_DIR"]
        else:
            os.environ["TIKTOKEN_CACHE_DIR"] = previous


def split_tokens(text: str, size: int = CHUNK_TOKENS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Slide a token window over the text, keeping `overlap` tokens between windows."""
    encoder = tokenizer()
    tokens = encoder.encode(text)
    if len(tokens) <= size:
        return [text]
    step = size - overlap
    windows = [tokens[start : start + size] for start in range(0, len(tokens), step)]
    return [encoder.decode(window) for window in windows if window]


def chunk_documents(
    documents: list[Document], size: int = CHUNK_TOKENS, overlap: int = CHUNK_OVERLAP
) -> tuple[list[Chunk], ChunkStats]:
    """Turn documents into de-duplicated chunks, with counters for the caller to report."""
    chunks: list[Chunk] = []
    seen: set[str] = set()
    generated = 0
    for document in documents:
        for piece in split_tokens(document.text, size, overlap):
            generated += 1
            digest = text_hash(piece)
            if digest in seen:
                continue
            seen.add(digest)
            chunks.append(
                Chunk(
                    id=digest,
                    text=piece,
                    url=document.url,
                    title=document.title,
                    section=document.section,
                    route=document.route.value,
                )
            )
    return chunks, ChunkStats(generated=generated, discarded=generated - len(chunks))
