"""Prompt loading.

Prompts are markdown files next to this module, each with a `version` front matter line. Nothing
in the codebase carries a prompt literal, and because these files are Brazilian Portuguese while
the code is English, no module ends up mixing languages.
"""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Prompt:
    """A versioned prompt, identified by file name."""

    name: str
    version: str
    text: str


@cache
def load_prompt(name: str) -> Prompt:
    """Load the prompt called `name` from this package."""
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt not found: {path}")
    version, text = _split_front_matter(path.read_text(encoding="utf-8"))
    return Prompt(name=name, version=version, text=text)


def _split_front_matter(raw: str) -> tuple[str, str]:
    """Separate the `version: x` front matter from the prompt body."""
    if not raw.startswith("---\n"):
        return "unversioned", raw.strip()
    front, _, body = raw[4:].partition("\n---\n")
    version = "unversioned"
    for line in front.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "version":
            version = value.strip()
    return version, body.strip()
