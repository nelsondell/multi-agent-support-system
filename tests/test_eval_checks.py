"""The `fx` eval check applies the web answer path's rule for amounts in reais."""

from types import SimpleNamespace

from app.prompts import load_prompt
from evals.checks import fx_in_reais

USD_TABLE = "| Euro | 1,1205 | -0,10% | | Libra esterlina | 1,3353 | -0,18% |"
BRL_TABLE = "| Euro (BC) - (R$/€) | 5,6483 | 5,6494 | 0,15% |"


def fx(answer: str, evidence: list[str]) -> bool | None:
    return fx_in_reais({"fx": True}, {"answer": answer}, SimpleNamespace(evidence=evidence))


def test_a_dollar_rate_given_as_reais_fails() -> None:
    assert fx("Hoje, 1 EUR = R$ 1,1205.", [USD_TABLE]) is False


def test_a_rate_the_evidence_gives_in_reais_passes() -> None:
    assert fx("Hoje, 1 EUR = R$ 5,6483.", [BRL_TABLE]) is True


def test_a_rate_with_no_currency_fails() -> None:
    assert fx("A cotação do euro é 5,6483.", [BRL_TABLE]) is False


def test_not_found_passes() -> None:
    assert fx(load_prompt("web_unavailable").text, [USD_TABLE]) is True


def test_a_question_that_is_not_about_a_rate_is_not_checked() -> None:
    assert fx_in_reais({}, {"answer": "R$ 1,00"}, SimpleNamespace(evidence=[])) is None
