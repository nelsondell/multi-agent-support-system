"""Structured logging.

JSON on stdout, with masking wired in as a processor rather than left to each call site: a node
cannot log an unmasked value even by accident. The merchant id is pseudonymised, so traces can be
correlated without carrying the identifier itself.
"""

import hashlib
import logging
import sys
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

import structlog

PSEUDONYM_PREFIX = "u_"

TECHNICAL_FIELDS = frozenset({"trace_id", "protocol", "timestamp"})
"""Values this system generates, never masked: a trace id that loses digits can no longer be
searched for. `conversation_id` is not one of them, because a client may choose it."""


def pseudonymize(user_id: str) -> str:
    """A stable, non-reversible handle for one merchant."""
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    return f"{PSEUDONYM_PREFIX}{digest[:12]}"


def mask_event(_logger: Any, _name: str, event: dict[str, Any]) -> dict[str, Any]:
    """Mask every string in the event but the technical ids, and never let a raw id through as
    `user_id`."""
    from app.observability.masking import mask_pii

    if "user_id" in event:
        event["user_id"] = pseudonymize(str(event.pop("user_id")))
    return {
        key: mask_pii(value) if isinstance(value, str) and key not in TECHNICAL_FIELDS else value
        for key, value in event.items()
    }


def configure_logging(level: int = logging.INFO) -> None:
    """Install the JSON pipeline. Idempotent, so tests and the API can both call it."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            mask_event,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> Any:
    """A logger whose output is masked by construction."""
    return structlog.get_logger(name)


def instrument_node(name: str, node: Callable[[Any], Awaitable[dict]]) -> Callable:
    """Wrap a graph node so every one of them emits the same event, with the same fields.

    Timing, agent name, routing decision and outcome are recorded here rather than inside the
    nodes: an agent that forgets to log cannot exist.
    """
    logger = get_logger("graph")

    async def instrumented(state: Any) -> dict:
        started = perf_counter()
        update = await node(state)
        decision = update.get("route") or getattr(state, "route", None)
        logger.info(
            "node.completed",
            agent=name,
            latency_ms=round((perf_counter() - started) * 1000, 2),
            trace_id=getattr(state, "trace_id", None),
            user_id=getattr(state, "user_id", None),
            intent=decision.intent.value if decision else None,
            plan=",".join(step.agent.value for step in decision.steps) if decision else None,
            router_confidence=decision.confidence if decision else None,
            guardrail=_value(update.get("guardrail_outcome")),
            refusal_reason=update.get("refusal_reason"),
            handoff_reason=update.get("handoff_reason"),
            escalated=update.get("escalation") is not None,
        )
        return update

    return instrumented


def _value(outcome: Any) -> str | None:
    """Enum members are logged by value, not by repr."""
    return outcome.value if outcome is not None else None
