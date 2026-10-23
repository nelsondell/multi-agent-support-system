"""Prometheus metrics for the operational questions: why do turns leave the automated path,
where does the time go, what does a resolution cost, and are the vendors failing.

Node latency and escalations are recorded in `instrument_node`, so no node can forget them;
tokens and cost in the model provider; turn outcomes in `/chat` and the operator resume. A
resolution is a turn answered without a human. Cost per resolution is spend over resolutions,
so the cost of the turns that escalated is carried by the ones that did not.
"""

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
