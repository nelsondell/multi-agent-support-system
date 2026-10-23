"""Prometheus metrics for the operational questions: why do turns leave the automated path,
where does the time go, what does a resolution cost, and are the vendors failing.

Node latency and escalations are recorded in `instrument_node`, so no node can forget them;
tokens and cost in the model provider; turn outcomes in `/chat` and the operator resume. A
resolution is a turn answered without a human. Cost per resolution is spend over resolutions,
so the cost of the turns that escalated is carried by the ones that did not.

The same calls are also counted per turn (`metered`), which is what the turn's budgets read.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from prometheus_client import Counter, Histogram

ESCALATIONS = Counter(
    "support_escalations", "Turns handed to a human, by typed reason.", ["reason"]
)
TURNS = Counter(
    "support_turns",
    "Merchant turns by intent and outcome: resolved, escalated, awaiting_confirmation, "
    "resolved_by_human.",
    ["intent", "outcome"],
)
MODEL_TOKENS = Counter(
    "support_model_tokens", "Chat model tokens by kind: input, cached, output.", ["kind"]
)
MODEL_COST = Counter("support_model_cost_usd", "Chat model spend, from tokens and LLM_PRICE_*.")
VENDOR_FAILURES = Counter(
    "support_vendor_failures",
    "Vendor calls that failed after their retries, by vendor and kind.",
    ["vendor", "kind"],
)
NODE_LATENCY = Histogram(
    "support_node_latency_seconds",
    "Wall time of each graph node.",
    ["node"],
    buckets=(0.005, 0.05, 0.25, 0.5, 1, 2, 4, 8, 16),
)


def record(node: str, seconds: float, update: dict) -> None:
    """Count one node run, and the escalation it produced, if any."""
    NODE_LATENCY.labels(node=node).observe(seconds)
    escalation = update.get("escalation")
    if node == "escalate" and escalation is not None:
        ESCALATIONS.labels(reason=escalation.reason.value).inc()


def record_turn(intent: str, status: str, escalated: bool) -> None:
    """Count a turn by what it ended as."""
    if status == "pending_human":
        outcome = "escalated"
    elif status == "pending_confirmation":
        outcome = "awaiting_confirmation"
    else:
        outcome = "resolved_by_human" if escalated else "resolved"
    TURNS.labels(intent=intent, outcome=outcome).inc()


@dataclass
class Spend:
    """What one turn has spent on the model so far."""

    model_calls: int = 0
    cost_usd: float = 0.0


_SPEND: ContextVar[Spend | None] = ContextVar("turn_spend", default=None)


@contextmanager
def metered() -> Iterator[Spend]:
    """Open one turn's spend: every model call made inside it, in any node, counts against it."""
    token = _SPEND.set(Spend())
    try:
        yield _SPEND.get()
    finally:
        _SPEND.reset(token)


def charge(*, calls: int = 0, cost_usd: float = 0.0) -> None:
    """Add to the current turn's spend; outside a turn there is nothing to charge."""
    spend = _SPEND.get()
    if spend is not None:
        spend.model_calls += calls
        spend.cost_usd += cost_usd


def turn_spend() -> Spend | None:
    """The current turn's spend, or None outside a metered turn."""
    return _SPEND.get()
