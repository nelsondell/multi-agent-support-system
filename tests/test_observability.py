"""Masking, pseudonymisation and the log pipeline."""

import json
import random

import pytest

from app.observability import configure_logging, get_logger, mask_pii, pseudonymize
from app.observability.logging import mask_event


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("meu cpf é 123.456.789-00", "[CPF]"),
        ("cpf 12345678900", "[CPF]"),
        ("cnpj 12.345.678/0001-99", "[CNPJ]"),
        ("cnpj 12345678000199", "[CNPJ]"),
        ("cartão 4111 1111 1111 1111", "[CARTAO]"),
        ("cartao 4111-1111-1111-1111", "[CARTAO]"),
        ("ligue (51) 99876-5432", "[TELEFONE]"),
        ("telefone 5199876543", "[TELEFONE]"),
    ],
)
def test_every_pii_class_is_masked(raw: str, expected: str) -> None:
    """One test per identifier the system may receive."""
    masked = mask_pii(raw)

    assert expected in masked
    assert not any(character.isdigit() for character in masked)


def test_masking_leaves_ordinary_numbers_alone() -> None:
    """Amounts and dates are not identifiers and must survive."""
    assert mask_pii("a venda de R$ 149,90 em 2026-08-31") == "a venda de R$ 149,90 em 2026-08-31"


def test_pseudonym_is_stable_and_hides_the_id() -> None:
    """Traces can be correlated without carrying the merchant id."""
    handle = pseudonymize("cliente1988")

    assert handle == pseudonymize("cliente1988")
    assert handle != pseudonymize("cliente7771")
    assert "cliente1988" not in handle


def test_the_processor_masks_and_pseudonymises_every_event() -> None:
    """A node cannot log an unmasked value, because the pipeline rewrites it."""
    event = mask_event(None, "info", {"user_id": "cliente1988", "message": "cpf 123.456.789-00"})

    assert event["user_id"].startswith("u_")
    assert "cliente1988" not in event["user_id"]
    assert event["message"] == "cpf [CPF]"


def test_emitted_line_is_json_with_the_operational_fields(capsys) -> None:
    """What lands on stdout is one machine-readable object per event."""
    configure_logging()
    get_logger("test").info(
        "node.completed",
        agent="support",
        latency_ms=12.5,
        trace_id="abc123",
        user_id="cliente1988",
        message="meu cartão 4111 1111 1111 1111",
    )

    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["agent"] == "support"
    assert line["latency_ms"] == 12.5
    assert line["trace_id"] == "abc123"
    assert line["user_id"].startswith("u_")
    assert "4111" not in line["message"]


TRACE_ID = "4521987654d4418b8a341127817bef7d"
"""A real shape: a uuid4 hex whose first ten digits once read as a phone number."""


def test_a_trace_id_reaches_the_log_as_generated() -> None:
    event = mask_event(None, "info", {"event": "node.completed", "trace_id": TRACE_ID})

    assert event["trace_id"] == TRACE_ID


def test_digits_inside_a_hex_id_are_never_a_phone() -> None:
    assert mask_pii(f"conversa {TRACE_ID}") == f"conversa {TRACE_ID}"


def test_a_protocol_shaped_like_a_phone_survives_while_the_phone_beside_it_does_not() -> None:
    masked = mask_pii("protocolo ESC-1198765432, telefone (51) 99876-5432")

    assert masked == "protocolo ESC-1198765432, telefone [TELEFONE]"


def test_no_generated_id_is_ever_masked() -> None:
    """Thousands of ids in the formats the system issues, none of which masking may touch."""
    generator = random.Random(7)
    hex_ids = [f"{generator.getrandbits(128):032x}" for _ in range(2000)]
    protocols = [f"ESC-{generator.getrandbits(40):010X}" for _ in range(2000)]

    assert [mask_pii(value) for value in hex_ids + protocols] == hex_ids + protocols
