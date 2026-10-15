"""The per-conversation lock: one turn at a time on a thread, and nothing left behind."""

import asyncio

import pytest

from app.locks import ConversationLocks


async def test_one_conversation_runs_one_block_at_a_time() -> None:
    locks = ConversationLocks()
    order: list[str] = []

    async def turn(name: str) -> None:
        async with locks.hold("cliente1988:c1"):
            order.append(f"{name} in")
            await asyncio.sleep(0.01)
            order.append(f"{name} out")

    await asyncio.gather(turn("first"), turn("second"))

    assert order == ["first in", "first out", "second in", "second out"]
    assert len(locks) == 0


async def test_a_turn_that_fails_releases_its_conversation() -> None:
    locks = ConversationLocks()

    with pytest.raises(RuntimeError):
        async with locks.hold("cliente1988:c1"):
            raise RuntimeError("graph failed")

    async with asyncio.timeout(1):
        async with locks.hold("cliente1988:c1"):
            pass
    assert len(locks) == 0
