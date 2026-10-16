"""PII masking.

Applied at the edge of the system and again inside the log pipeline, so a value can only be
logged unmasked if it never passed through either — which no node can arrange, because the
processor runs on every event this application emits.

Masking never touches the system's own identifiers. A personal number is never glued to letters,
so a digit run inside a hex trace id or conversation id is not one; and a case protocol or a
transaction id is skipped whole, because "ESC-1198765432" is shaped exactly like a phone number.
"""

import re

_CARD = re.compile(r"\b(?:\d[ .-]?){13,16}\b")
_CNPJ = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_PHONE = re.compile(
    r"(?<![0-9A-Za-z])(?:\+55\s?)?\(?\d{2}\)?[\s.-]?9?\d{4}[\s.-]?\d{4}(?![0-9A-Za-z])"
)
_SYSTEM_ID = re.compile(r"\b(ESC-[0-9A-F]{10}|TX-\d{8}-\d{4})\b")
"""Case protocols (`mint_protocol`) and transaction ids, in the formats this system issues."""

CARD_MASK = "[CARTAO]"
CNPJ_MASK = "[CNPJ]"
CPF_MASK = "[CPF]"
PHONE_MASK = "[TELEFONE]"


def mask_pii(text: str) -> str:
    """Replace card, CNPJ, CPF and phone numbers with typed placeholders.

    Order matters: the longer identifiers are matched first, otherwise a CNPJ would be partly
    consumed by the card pattern and leak its remaining digits.
    """
    if not text:
        return text
    # Splitting on a capturing group keeps the system ids at the odd positions, untouched.
    parts = _SYSTEM_ID.split(text)
    return "".join(part if index % 2 else _mask(part) for index, part in enumerate(parts))


def _mask(text: str) -> str:
    """Every personal number in a stretch of text that holds no system id."""
    masked = _CNPJ.sub(CNPJ_MASK, text)
    masked = _CARD.sub(CARD_MASK, masked)
    masked = _CPF.sub(CPF_MASK, masked)
    return _PHONE.sub(PHONE_MASK, masked)
