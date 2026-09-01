"""Input guardrail.

Runs before routing and is deliberately deterministic: patterns, no model call. It is defence in
depth for identity, and the only thing in the system that can answer an out-of-scope question,
since there is no web search to fall back on.
"""

import re
from typing import Any

from app.contracts import GuardrailOutcome
from app.graph.state import GraphState
from app.observability import get_logger, mask_pii
from app.prompts import load_prompt

logger = get_logger(__name__)

_INJECTION = [
    re.compile(r"\bignor(e|ar|e todas)\b.{0,30}\b(instru|regra|prompt)", re.IGNORECASE),
    re.compile(r"\bdesconsider(e|ar)\b.{0,30}\b(instru|regra)", re.IGNORECASE),
    re.compile(r"\besque(ça|ca|cer)\b.{0,20}\b(instru|regra)", re.IGNORECASE),
    re.compile(r"\bignore\b.{0,30}\b(previous|above|instructions|rules)", re.IGNORECASE),
    re.compile(r"\b(system|developer)\s+prompt\b", re.IGNORECASE),
    re.compile(r"\b(aja|atue|comporte-se)\s+como\b", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE),
]

_IDENTITY = [
    re.compile(
        r"\b(conta|dados|vendas|cadastro|extrato)\b.{0,30}\bdo\s+(cliente|usu[áa]rio)\s+\S+"
    ),
    re.compile(r"\buser[_\s-]?id\b\s*[:=]?\s*\S+", re.IGNORECASE),
    re.compile(r"\bcliente\d{3,}\b", re.IGNORECASE),
    re.compile(r"\b(de\s+outro|do\s+outro)\s+(cliente|estabelecimento|lojista)\b", re.IGNORECASE),
]

_OUT_OF_SCOPE = [
    # Both languages: the challenge's out-of-scope scenarios arrive in English, while real
    # merchants write Portuguese. Missing either half makes the guardrail decorative.
    re.compile(r"\b(previs[ãa]o do tempo|clima|temperatura|vai chover)\b", re.IGNORECASE),
    re.compile(r"\b(weather|forecast|temperature|going to rain)\b", re.IGNORECASE),
    re.compile(r"\b(cota[çc][ãa]o|c[âa]mbio)\b", re.IGNORECASE),
    re.compile(r"\b(exchange rate|currency rate)\b", re.IGNORECASE),
    re.compile(
        r"\b(d[óo]lar|euro|dollar|bitcoin)\b.{0,25}\b(hoje|agora|cota|today|rate)\b", re.IGNORECASE
    ),
    re.compile(r"\b(receita de|quem ganhou|piada|who won|recipe for)\b", re.IGNORECASE),
]


def classify(message: str) -> GuardrailOutcome:
    """Decide before any model sees the message."""
    if any(pattern.search(message) for pattern in _INJECTION):
        return GuardrailOutcome.IDENTITY_SWAP
    if any(pattern.search(message) for pattern in _IDENTITY):
        return GuardrailOutcome.IDENTITY_SWAP
    if any(pattern.search(message) for pattern in _OUT_OF_SCOPE):
        return GuardrailOutcome.OUT_OF_DOMAIN
    return GuardrailOutcome.ALLOW


_REFUSALS = {
    GuardrailOutcome.OUT_OF_DOMAIN: "out_of_domain",
    GuardrailOutcome.IDENTITY_SWAP: "identity_refusal",
}


async def inspect(state: GraphState) -> dict[str, Any]:
    """Mask the message for logging, then classify it."""
    outcome = classify(state.message)
    masked = mask_pii(state.message)
    update: dict[str, Any] = {"guardrail_outcome": outcome, "masked_message": masked}
    if outcome is not GuardrailOutcome.ALLOW:
        logger.info("guardrail.blocked", outcome=outcome.value, message=masked)
        update["answer"] = load_prompt(_REFUSALS[outcome]).text
    return update


def after_guardrail(state: GraphState) -> str:
    """A blocked message is answered here and never reaches an agent."""
    return "router" if state.guardrail_outcome is GuardrailOutcome.ALLOW else "done"
