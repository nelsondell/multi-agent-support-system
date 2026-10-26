"""Hard rule 4: text the system rephrases may not carry a number its source did not.

Numbers are compared by value, never by spelling. A token is read under the Brazilian convention
("." groups thousands, "," marks decimals) and the English one (the reverse), keeping only the
readings whose digit groups are well formed, so "R$ 1.234,56" and "1234.56" are both 1234.56. A
token with one ambiguous separator ("1.234") keeps both readings. A generated number passes when
any of its readings matches any reading of a number in the source: that leniency only accepts a
value the source already contains under some reading, never one it lacks.

An amount given in reais must also be one a source writes next to the real ("R$", "real", "reais"
or "BRL"): the same digits in a column of dollar rates do not back it.
"""

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation

_NUMBER = re.compile(r"\d[\d.,]*\d|\d")
_GROUPED = re.compile(r"\d{1,3}(?:G\d{3})+")


def _reading(token: str, group: str, decimal: str) -> Decimal | None:
    """The value of `token` with `group` as thousands separator and `decimal` as decimal mark,
    or None when the token is not well formed under that convention."""
    whole, _, fraction = token.partition(decimal)
    if decimal in fraction or group in fraction:
        return None
    if group in whole and not _GROUPED.fullmatch(whole.replace(group, "G")):
        return None
    digits = whole.replace(group, "")
    try:
        return Decimal(f"{digits}.{fraction}" if fraction else digits)
    except InvalidOperation:
        return None


def readings(token: str) -> set[Decimal]:
    """Every value a numeric token can mean, normalized so 1.50 and 1.5 are equal."""
    values = {_reading(token, ".", ","), _reading(token, ",", ".")}
    return {value.normalize() for value in values if value is not None}


def numbers_in(text: str) -> list[set[Decimal]]:
    """The possible values of each number written in `text`."""
    return [readings(token) for token in _NUMBER.findall(text)]


def adds_numbers(generated: str, sources: list[str], quoting: Iterable[str] = ()) -> bool:
    """Whether `generated` contains a number that appears in none of `sources`.

    `quoting` lists identifiers, such as a case protocol, that the text may repeat verbatim. They
    are taken out of `generated` before its numbers are read, and they never vouch for a number:
    the digits inside "ESC-5DAAB3D48A" must not let a "3 dias" through.
    """
    for identifier in quoting:
        generated = generated.replace(identifier, " ")
    known = set().union(*(values for source in sources for values in numbers_in(source)))
    return any(not (values & known) for values in numbers_in(generated))


_STATED_IN_REAIS = re.compile(r"R\$\s*(\d[\d.,]*\d|\d)|(\d[\d.,]*\d|\d)\s*(?:reais|real)\b")
_REAIS = re.compile(r"R\$|\bBRL\b|\breais\b|\breal\b", re.IGNORECASE)
_NEAR = 24
"""How far, in characters with no digit in between, a currency marker may sit from its number:
enough for a table row's label, as in "| Euro (BC) - (R$/€) | 5,6483"."""


def stated_in_reais(text: str) -> list[set[Decimal]]:
    """The amounts `text` gives in reais: "R$ 5,64" or "5,64 reais"."""
    return [readings(match.group(1) or match.group(2)) for match in _STATED_IN_REAIS.finditer(text)]


def next_to_reais(text: str) -> list[set[Decimal]]:
    """The numbers `text` writes next to "R$", "real", "reais" or "BRL": the marker within a few
    characters before or after, with no other number in between."""
    amounts = []
    for match in _NUMBER.finditer(text):
        before = re.split(r"\d", text[max(0, match.start() - _NEAR) : match.start()])[-1]
        after = re.split(r"\d", text[match.end() : match.end() + _NEAR])[0]
        if _REAIS.search(before) or _REAIS.search(after):
            amounts.append(readings(match.group()))
    return amounts


def adds_reais(generated: str, sources: list[str]) -> bool:
    """Whether `generated` gives an amount in reais that no source writes next to the real.

    A number the source does carry is not enough: "Euro | 1,1205" in a table of dollar rates must
    not come out as "1 EUR = R$ 1,1205".
    """
    known = set().union(*(values for source in sources for values in next_to_reais(source)))
    return any(not (values & known) for values in stated_in_reais(generated))
