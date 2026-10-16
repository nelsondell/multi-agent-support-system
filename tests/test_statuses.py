"""Hard rule 4 for status words: a rephrasing may not flip what happened to a sale or a payment."""

import pytest

from app.graph.statuses import adds_statuses, statuses_in


@pytest.mark.parametrize(
    ("generated", "source"),
    [
        ("A venda de R$ 89,50 foi aprovada.", "A venda de R$ 89,50 foi estornada."),
        ("O estorno foi aprovado.", "O estorno foi negado."),
        ("O valor já foi liquidado.", "O valor está previsto para 02/09."),
        ("A maquininha está ativa.", '{"status": "sem_comunicacao"}'),
    ],
)
def test_a_flipped_status_is_caught(generated: str, source: str) -> None:
    assert adds_statuses(generated, [source])


@pytest.mark.parametrize(
    ("generated", "source"),
    [
        ("Suas vendas foram recusadas pelo emissor.", "A venda foi negada pelo emissor."),
        ("As duas transações estornadas somam R$ 89,50.", "Transação estornada: R$ 89,50."),
        ("O valor foi creditado na sua conta.", '{"status": "liquidado"}'),
        ("A maquininha está sem comunicação.", '{"status": "sem_comunicacao"}'),
        ("Você pode vender pelo link de pagamento.", "O link de pagamento aceita cartão."),
    ],
)
def test_synonyms_gender_and_number_do_not_count_as_a_change(generated: str, source: str) -> None:
    assert not adds_statuses(generated, [source])


def test_a_status_is_read_as_a_word_not_inside_one() -> None:
    """Whole words only: "inativa" is offline, "previsão" names no status, case is ignored."""
    assert statuses_in("Conta inativa, previsão de chuva.") == {"offline"}
    assert statuses_in("Aprovada!") == {"approved"}
