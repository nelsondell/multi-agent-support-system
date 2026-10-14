"""Optional LangSmith tracing, off by default, masked when on.

LangSmith's switch is global and set once at start-up. On, every trace goes through one client
whose inputs and outputs pass through `mask_pii` before upload. Off, the switch is set to off
rather than left alone, because LangChain would otherwise start its own tracer, unmasked, from a
`LANGSMITH_TRACING=true` it finds in the environment. Off costs nothing per request: no callback,
no client. Uploads run in LangSmith's background threads and a tracer error is caught by
LangChain, so tracing can never fail a request.
"""

import logging
from typing import Any

import langsmith

from app.observability.masking import mask_pii
from app.settings import Settings

logger = logging.getLogger(__name__)


def masked(value: Any) -> Any:
    """The same payload with every string masked, however deep; anything else as text, masked."""
    if isinstance(value, str):
        return mask_pii(value)
    if isinstance(value, dict):
        return {key: masked(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [masked(item) for item in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    return mask_pii(str(value))


def configure_tracing(settings: Settings) -> bool:
    """Turn LangSmith tracing on through a masking client, or firmly off; True when on."""
    if settings.langsmith_tracing and not settings.langsmith_api_key:
        logger.warning("LANGSMITH_TRACING is on but LANGSMITH_API_KEY is unset; tracing stays off")
    if not (settings.langsmith_tracing and settings.langsmith_api_key):
        langsmith.configure(enabled=False)
        return False
    client = langsmith.Client(
        api_key=settings.langsmith_api_key, hide_inputs=masked, hide_outputs=masked
    )
    langsmith.configure(client=client, enabled=True, project_name=settings.langsmith_project)
    return True
