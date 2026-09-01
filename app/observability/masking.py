"""PII masking.

Applied at the edge of the system and again inside the log pipeline, so a value can only be
logged unmasked if it never passed through either — which no node can arrange, because the
processor runs on every event this application emits.
"""

import re

_CARD = re.compile(r"\b(?:\d[ .-]?){13,16}\b")
_CNPJ = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+55\s?)?\(?\d{2}\)?[\s.-]?9?\d{4}[\s.-]?\d{4}(?!\d)")

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
    masked = _CNPJ.sub(CNPJ_MASK, text)
    masked = _CARD.sub(CARD_MASK, masked)
    masked = _CPF.sub(CPF_MASK, masked)
    return _PHONE.sub(PHONE_MASK, masked)
