"""Hard rule 4: numbers are compared by value, whichever convention wrote them."""

from decimal import Decimal

import pytest

from app.graph.numbers import adds_numbers, readings


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("1.234,56", {Decimal("1234.56")}),
        ("1234.56", {Decimal("1234.56")}),
        ("1,234.56", {Decimal("1234.56")}),
        ("2,5", {Decimal("2.5")}),
        ("09", {Decimal("9")}),
        ("1.234", {Decimal("1234"), Decimal("1.234")}),
        ("12.34.5", set()),
    ],
)
def test_a_token_reads_as_every_value_its_separators_allow(token: str, expected: set) -> None:
    assert readings(token) == expected


def test_the_same_value_in_another_convention_adds_nothing() -> None:
    assert not adds_numbers("Estorno de 1234.56 aprovado.", ["Estorno de R$ 1.234,56 aprovado"])


def test_a_value_the_source_lacks_is_added() -> None:
    assert adds_numbers("Cai em 3 dias úteis.", ["Estorno aprovado, cai em 2 dias úteis"])


def test_a_quoted_protocol_never_vouches_for_a_number() -> None:
    protocol = "ESC-3B500C0D1E"

    assert adds_numbers("Cai em 3 dias úteis.", ["Cai em 2 dias úteis."], quoting=[protocol])
    assert not adds_numbers(
        f"Protocolo {protocol}: cai em 2 dias úteis.", ["Cai em 2 dias úteis."], quoting=[protocol]
    )


def test_text_without_numbers_adds_nothing() -> None:
    assert not adds_numbers("Estorno aprovado.", ["Estorno aprovado, cai em 2 dias úteis"])
