"""Hard rule 4, for words: text the system rephrases may not state a status its source did not.

Keeping every number is not enough. "A venda de R$ 89,50 foi estornada" reworded as "foi aprovada"
carries the same numbers and the opposite fact. Each status word is read as the status it names,
whatever its gender or number, with synonyms folded together ("negada" and "recusada" are one
status), and a rephrasing passes only when every status it states is one its sources state too.
The vocabulary is the account data's: transaction, settlement and terminal statuses.
"""

import re
from collections.abc import Iterable

_WORDS = {
    "approved": r"aprovad[oa]s?",
    "declined": r"negad[oa]s?|recusad[oa]s?",
    "refunded": r"estornad[oa]s?|estornos?",
    "cancelled": r"cancelad[oa]s?",
    "blocked": r"bloquead[oa]s?",
    "settled": r"liquidad[oa]s?|creditad[oa]s?|depositad[oa]s?",
    "scheduled": r"previst[oa]s?|agendad[oa]s?",
    "online": r"ativ[oa]s?|online",
    "offline": r"sem[\s_]comunica[çc][ãa]o|offline|inativ[oa]s?",
}
_STATUSES = {
    status: re.compile(rf"\b(?:{words})\b", re.IGNORECASE) for status, words in _WORDS.items()
}


def statuses_in(text: str) -> set[str]:
    """The statuses `text` states."""
    return {status for status, pattern in _STATUSES.items() if pattern.search(text)}


def adds_statuses(generated: str, sources: Iterable[str]) -> bool:
    """Whether `generated` states a status that none of `sources` states."""
    known = set().union(*(statuses_in(source) for source in sources))
    return bool(statuses_in(generated) - known)
