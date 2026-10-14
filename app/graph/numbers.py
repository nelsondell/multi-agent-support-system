"""Hard rule 4: text the system rephrases may not carry a number its source did not.

Numbers are compared by value, never by spelling. A token is read under the Brazilian convention
("." groups thousands, "," marks decimals) and the English one (the reverse), keeping only the
readings whose digit groups are well formed, so "R$ 1.234,56" and "1234.56" are both 1234.56. A
token with one ambiguous separator ("1.234") keeps both readings. A generated number passes when
any of its readings matches any reading of a number in the source: that leniency only accepts a
value the source already contains under some reading, never one it lacks.
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
