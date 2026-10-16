"""Conversation memory: which thread a turn belongs to, what a turn starts from, and what it
leaves behind.

No node touches the store. The checkpointer is attached when the graph is compiled, the turn
input puts every per-turn field back at its default, and `remember` is the only node that writes
the history.
"""

import hashlib
import json
import logging
from datetime import date
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, RemoveMessage
from pydantic import BaseModel

from app.graph.state import GraphState
from app.observability import mask_pii
from app.prompts import load_prompt
from app.providers.llm import LLMProvider, LLMProviderError

logger = logging.getLogger(__name__)

CONVERSATION_FIELDS = frozenset({"messages", "summary"})
"""The only state that outlives a turn. Everything else is reset by `turn_input`."""


class ConversationSummary(BaseModel):
    """The rolling summary after the oldest messages were folded into it."""

    summary: str


def thread_id(user_id: str, conversation_id: str) -> str:
    """The checkpoint key of one merchant's conversation.

    Both values are encoded as a JSON pair, so no choice of strings makes two different pairs
    collide, then hashed, so the raw merchant id never becomes a storage key. The same
    conversation id sent with another merchant's id is simply a different, empty thread.
    """
    pair = json.dumps([user_id, conversation_id], ensure_ascii=False)
    return hashlib.sha256(pair.encode("utf-8")).hexdigest()


def turn_input(
    *, message: str, user_id: str, conversation_id: str, trace_id: str, today: date
) -> dict[str, Any]:
    """The input of one turn, with every per-turn field explicitly back at its default.

    A checkpointed invocation merges its input into the stored state, so a field left out here
    would carry the previous turn's answer, sources or hop count into this one.
    """
    fresh = {
        name: field.get_default(call_default_factory=True)
        for name, field in GraphState.model_fields.items()
        if name not in CONVERSATION_FIELDS and not field.is_required()
    }
    return {
        **fresh,
        "message": message,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "trace_id": trace_id,
        "today": today,
    }


async def remember(
    state: GraphState, provider: LLMProvider, window: int, limit: int
) -> dict[str, Any]:
    """Append this exchange to the history, folding the oldest messages into the summary once
    the history would outgrow `limit`. The `window` most recent messages always stay verbatim."""
    reply = "\n\n".join(part for part in (state.partial_answer, state.answer) if part)
    exchange = [
        HumanMessage(content=state.masked_message or mask_pii(state.message)),
        AIMessage(content=reply),
    ]
    total = len(state.messages) + len(exchange)
    if total <= limit:
        return {"messages": exchange}

    oldest = state.messages[: total - window]
    summary = await _fold(state.summary, oldest, provider)
    if summary is None:
        return {"messages": exchange}
    removals = [RemoveMessage(id=message.id) for message in oldest]
    return {"messages": [*removals, *exchange], "summary": summary}


async def _fold(summary: str, messages: list[AnyMessage], provider: LLMProvider) -> str | None:
    """One structured call merges the messages into the summary. None when the call fails, so
    the caller keeps the history whole and the fold is retried on the next turn."""
    payload = {
        "summary": summary,
        "messages": [{"role": message.type, "content": message.content} for message in messages],
    }
    try:
        folded = await provider.structured(
            system=load_prompt("summary").text,
            user=json.dumps(payload, ensure_ascii=False),
            schema=ConversationSummary,
        )
    except LLMProviderError as exc:
        logger.warning("summary fold failed, history kept whole: %s", exc)
        return None
    return folded.summary
