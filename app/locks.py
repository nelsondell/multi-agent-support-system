"""One turn at a time per conversation.

Two messages on one conversation, or a message and an operator's answer, would otherwise run the
graph on the same thread at once: both read the same checkpoint and the later write drops the
earlier turn. Each conversation gets a lock, held from reading its state to writing its answer, so
a second request waits for the first and then sees its result. Other conversations never wait.

The locks live in this process, and a lock is dropped once no request holds or waits for it, so
the registry does not grow with every conversation ever seen. Replicas over Postgres share them
through the database (`PostgresLocks`).
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class ConversationLocks:
    """An asyncio lock per conversation thread, created on first use and dropped when idle."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._users: dict[str, int] = {}

    @asynccontextmanager
    async def hold(self, thread: str) -> AsyncIterator[None]:
        """Run the block alone on this thread, after every request already holding it."""
        lock = self._locks.setdefault(thread, asyncio.Lock())
        self._users[thread] = self._users.get(thread, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._users[thread] -= 1
            if not self._users[thread]:
                del self._users[thread], self._locks[thread]

    def __len__(self) -> int:
        """How many conversations have a request holding or waiting for their lock."""
        return len(self._locks)


class PostgresLocks:
    """An advisory lock per thread, which the server releases if the replica holding it dies."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @asynccontextmanager
    async def hold(self, thread: str) -> AsyncIterator[None]:
        async with self._pool.connection() as connection, connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (thread,)
            )
            yield
