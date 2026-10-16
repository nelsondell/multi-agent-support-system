"""Input guardrail.

Runs before routing and is deliberately deterministic: patterns, no model call. It refuses prompt
injection, and requests for another merchant's data as defence in depth for identity: an id in
the message is compared with the authenticated caller's, so naming your own is allowed. It does not
judge scope: a general-purpose question such as the weather or an exchange rate is routed, and the
knowledge agent answers it from a cited web search.
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

_NAMED_ID = [
    re.compile(
        r"\b(?:conta|dados|vendas|cadastro|extrato)\b.{0,30}"
        r"\bdo\s+(?:cliente|usu[áa]rio)\s+(?P<id>[\w-]+)"
    ),
    re.compile(r"\buser[_\s-]?id\b\s*[:=]?\s*(?P<id>[\w-]+)", re.IGNORECASE),
    re.compile(r"\b(?P<id>cliente\d{3,})\b", re.IGNORECASE),
]
_ANOTHER_MERCHANT = re.compile(
    r"\b(de\s+outro|do\s+outro)\s+(cliente|estabelecimento|lojista)\b", re.IGNORECASE
)


def names_another_merchant(message: str, user_id: str) -> bool:
    """Whether the message asks about a merchant other than the caller: one it calls another, or
    an id that is not the caller's. A merchant naming their own id is not swapping identity."""
    if _ANOTHER_MERCHANT.search(message):
        return True
    named = {match["id"].casefold() for pattern in _NAMED_ID for match in pattern.finditer(message)}
    return bool(named - {user_id.casefold()})


def classify(message: str, user_id: str) -> GuardrailOutcome:
    """Decide before any model sees the message. `user_id` is the authenticated caller."""
    if any(pattern.search(message) for pattern in _INJECTION):
        return GuardrailOutcome.PROMPT_INJECTION
    if names_another_merchant(message, user_id):
        return GuardrailOutcome.IDENTITY_SWAP
    return GuardrailOutcome.ALLOW


_REFUSALS = {
    GuardrailOutcome.PROMPT_INJECTION: "injection_refusal",
    GuardrailOutcome.IDENTITY_SWAP: "identity_refusal",
}


async def inspect(state: GraphState) -> dict[str, Any]:
    """Mask the message for logging, then classify it."""
    outcome = classify(state.message, state.user_id)
    masked = mask_pii(state.message)
    update: dict[str, Any] = {"guardrail_outcome": outcome, "masked_message": masked}
    if outcome is not GuardrailOutcome.ALLOW:
        logger.info("guardrail.blocked", outcome=outcome.value, message=masked)
        update["answer"] = load_prompt(_REFUSALS[outcome]).text
    return update


def after_guardrail(state: GraphState) -> str:
    """A blocked message is answered here and never reaches an agent."""
    return "router" if state.guardrail_outcome is GuardrailOutcome.ALLOW else "done"
