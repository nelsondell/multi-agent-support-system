"""Where a replica keeps what outlives a request: checkpoints, the handoff queue and the lock that
runs one turn at a time per conversation.

SQLite, the default, keeps them in files on one node. Postgres keeps them in one database every
replica shares, so a case escalated on one replica can be answered on another and a turn that
dies with its replica resumes on the next. The data pool and the lock pool are separate: a turn
holds a lock connection for its whole length, and must never wait on a data connection held by
another turn waiting for its lock.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.checkpoints import checkpoint_serializer, open_checkpointer
from app.handoffs import HandoffQueue, PostgresTables, open_handoff_queue
from app.locks import ConversationLocks, PostgresLocks
from app.settings import Settings

_SCHEMA_LOCK = 4_802_001
"""Advisory lock key for creating the tables, so replicas booting together take turns at it."""


async def create_tables(url: str, checkpointer: BaseCheckpointSaver, queue: HandoffQueue) -> None:
    """Create or migrate the tables, one replica at a time.

    LangGraph's migrations build indexes `CONCURRENTLY`, which waits for every open transaction,
    so the lock is a session lock taken by polling on an autocommit connection: no replica holds a
    transaction while another one migrates. Closing the connection releases the lock."""
    import psycopg

    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as connection:
        while not await _try_schema_lock(connection):
            await asyncio.sleep(0.2)
        await checkpointer.setup()
        await queue.create_table()


async def _try_schema_lock(connection) -> bool:
    cursor = await connection.execute("SELECT pg_try_advisory_lock(%s)", (_SCHEMA_LOCK,))
    return (await cursor.fetchone())[0]


@dataclass(frozen=True)
class Storage:
    checkpointer: BaseCheckpointSaver
    queue: HandoffQueue
    locks: ConversationLocks | PostgresLocks


@asynccontextmanager
async def open_storage(settings: Settings) -> AsyncIterator[Storage]:
    """Open the configured store, creating its tables on first use."""
    if settings.storage == "postgres":
        async with open_postgres(settings) as storage:
            yield storage
        return
    async with open_checkpointer(settings) as checkpointer, open_handoff_queue(settings) as queue:
        yield Storage(checkpointer, queue, ConversationLocks())


@asynccontextmanager
async def open_postgres(settings: Settings) -> AsyncIterator[Storage]:
    """Two pools on `DATABASE_URL`: data in autocommit with dict rows, as LangGraph's saver needs,
    and turn locks."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    if not settings.database_url:
        raise ValueError("STORAGE=postgres needs DATABASE_URL")
    size = settings.database_pool_size
    data = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}
    async with (
        AsyncConnectionPool(settings.database_url, max_size=size, kwargs=data, open=False) as pool,
        AsyncConnectionPool(settings.database_url, max_size=size, open=False) as lock_pool,
    ):
        checkpointer = AsyncPostgresSaver(pool, serde=checkpoint_serializer())
        queue = HandoffQueue(PostgresTables(pool))
        await create_tables(settings.database_url, checkpointer, queue)
        yield Storage(checkpointer, queue, PostgresLocks(lock_pool))
