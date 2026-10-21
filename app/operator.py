"""Operator endpoints: list the cases waiting for a human, read one, and answer it.

Every route requires `Authorization: Bearer <OPERATOR_TOKEN>`. Without a configured token they
answer 503 rather than open up. Answering a case resumes the paused graph on that case's thread,
and the agent turns the operator's input into the merchant's reply, which is held until the
merchant's next message delivers it. A case can also be cancelled, which closes it with a notice.
The resume holds the conversation's lock, so it never runs alongside a merchant's message.
"""

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from langgraph.types import Command

from app.contracts import ChatResponse, DeliveredReply, HandoffRecord, OperatorInput, Outcome
from app.responses import to_response


def require_operator(request: Request, authorization: str | None = Header(default=None)) -> None:
    """Admit only a request carrying the configured operator token."""
    expected = request.app.state.settings.operator_token
    if not expected:
        raise HTTPException(503, "operator endpoints are disabled: OPERATOR_TOKEN is not set")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(
        token.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            401, "missing or invalid operator token", headers={"WWW-Authenticate": "Bearer"}
        )


router = APIRouter(prefix="/handoffs", dependencies=[Depends(require_operator)])


@router.get("", response_model=list[HandoffRecord])
async def list_handoffs(request: Request) -> list[HandoffRecord]:
    """Every case still waiting for a human, oldest first."""
    return await request.app.state.queue.list_open()


@router.get("/{protocol}", response_model=HandoffRecord)
async def show_handoff(protocol: str, request: Request) -> HandoffRecord:
    """One case, open or closed."""
    found = await request.app.state.queue.get(protocol)
    if found is None:
        raise HTTPException(404, f"no case with protocol {protocol}")
    return found[0]


async def settle(
    app: Any, record: HandoffRecord, thread: str, decision: OperatorInput, outcome: Outcome
) -> ChatResponse | None:
    """Close a case, resume its conversation from the decision, and hold the reply for the
    merchant's next message. None when the case was already closed by someone else. The caller
    holds the conversation's lock."""
    queue, protocol = app.state.queue, record.case.protocol
    if not await queue.claim(protocol, outcome):
        return None
    config = {"configurable": {"thread_id": thread}}
    try:
        resume = Command(resume=decision.model_dump())
        final = await app.state.graph.ainvoke(resume, config, durability="sync")
    except Exception:
        await queue.reopen(protocol)
        raise
    response = to_response(final, record.case.conversation_id)
    await queue.hold_reply(
        DeliveredReply(protocol=protocol, answer=response.answer, sources=response.sources)
    )
    return response


@router.post("/{protocol}/resume", response_model=ChatResponse)
async def resume_handoff(protocol: str, decision: OperatorInput, request: Request) -> ChatResponse:
    """Answer a case (approve its draft or reply in the operator's words), or cancel it, and
    continue the conversation. The merchant receives the result with their next message."""
    found = await request.app.state.queue.get(protocol)
    if found is None:
        raise HTTPException(404, f"no case with protocol {protocol}")
    record, thread = found
    if record.status == "closed":
        raise HTTPException(409, f"case {protocol} is already closed")
    if decision.action == "approve" and not record.case.proposed_draft:
        raise HTTPException(422, f"case {protocol} has no draft to approve; reply instead")
    outcome = "cancelled" if decision.action == "cancel" else "answered"
    async with request.app.state.locks.hold(thread):
        response = await settle(request.app, record, thread, decision, outcome)
    if response is None:
        raise HTTPException(409, f"case {protocol} is already closed")
    return response
