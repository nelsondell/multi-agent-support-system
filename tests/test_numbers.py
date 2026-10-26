"""Hard rule 4: numbers are compared by value, whichever convention wrote them."""

from decimal import Decimal

import pytest

from app.graph.numbers import adds_numbers, adds_reais, readings


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


USD_TABLE = "| Euro | 1,1205 | -0,10% | 26/10/2026 | | Libra esterlina | 1,3353 | -0,18% |"
"""Valor's table of rates in dollars: the euro's digits, with no real anywhere near them."""
BRL_TABLE = "| Euro Turismo (R$/€) | 5,6996 | 5,8796 | | Euro (BC) - (R$/€) | 5,6483 | 5,6494 |"


def test_a_dollar_rate_given_as_reais_is_added() -> None:
    """S07: the digits are in the source, but not as reais."""
    assert not adds_numbers("O euro está em R$ 1,1205.", [USD_TABLE]), "the old rule lets it pass"
    assert adds_reais("O euro está em R$ 1,1205.", [USD_TABLE])
    assert adds_reais("Hoje, 1 EUR = R$ 1,1205.", [USD_TABLE])


@pytest.mark.parametrize(
    ("answer", "source"),
    [
        ("Hoje, 1 EUR = R$ 5,6483.", BRL_TABLE),
        ("O dólar está em R$ 4,98.", "O dólar americano é negociado na faixa de R$ 4,98 hoje."),
        ("1 EUR = R$ 5,5860.", "Cotação de referência: 5,5860 reais por 1 EUR."),
        ("1 EUR = R$ 5,5860.", "EUR/BRL 5,5860, atualizado às 10h."),
    ],
    ids=["label in the row", "R$ before", "reais after", "BRL before"],
)
def test_an_amount_the_source_writes_next_to_the_real_is_not_added(answer, source) -> None:
    assert not adds_reais(answer, [source])


def test_a_number_with_another_between_it_and_the_real_is_not_backed() -> None:
    """ "R$ 4,98" vouches for 4,98, not for the euro's 1,1205 later in the row."""
    assert adds_reais("1 EUR = R$ 1,1205.", ["Dólar R$ 4,98 | Euro 1,1205"])


def test_an_answer_with_no_amount_in_reais_adds_none() -> None:
    assert not adds_reais("Amanhã, máxima de 24° e mínima de 15°.", [USD_TABLE])
