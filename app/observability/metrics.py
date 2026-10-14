"""Prometheus metrics for the two operational questions: why do turns leave the automated path,
and where does the time go.

Both are recorded in `instrument_node`, so no node can forget them, and served by `GET /metrics`.
"""

from prometheus_client import Counter, Histogram

ESCALATIONS = Counter(
    "support_escalations", "Turns handed to a human, by typed reason.", ["reason"]
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
