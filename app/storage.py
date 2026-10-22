"""Checkpoints, the handoff queue and turn locks: SQLite files on one node, or one Postgres every
replica shares."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.audit import AuditLog
from app.checkpoints import checkpoint_serializer, open_checkpointer
from app.handoffs import HandoffQueue, PostgresTables, open_sqlite_tables
from app.locks import ConversationLocks, PostgresLocks
from app.settings import Settings
from app.tools.tickets import TicketDesk

_SCHEMA_LOCK = 4_802_001  # advisory lock key: replicas booting together create tables in turn


async def create_tables(url: str, checkpointer: BaseCheckpointSaver, *tables) -> None:
    """A polled session lock, not a transaction one: LangGraph indexes `CONCURRENTLY`, which waits
    for every open transaction."""
    import psycopg

    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as connection:
        while not await _try_schema_lock(connection):
            await asyncio.sleep(0.2)
        await checkpointer.setup()
        for table in tables:
            await table.create_table()


async def _try_schema_lock(connection) -> bool:
    cursor = await connection.execute("SELECT pg_try_advisory_lock(%s)", (_SCHEMA_LOCK,))
    return (await cursor.fetchone())[0]


@dataclass(frozen=True)
class Storage:
    checkpointer: BaseCheckpointSaver
    queue: HandoffQueue
    locks: ConversationLocks | PostgresLocks
    audit: AuditLog
    desk: TicketDesk


@asynccontextmanager
async def open_storage(settings: Settings) -> AsyncIterator[Storage]:
    if settings.storage == "postgres":
        async with open_postgres(settings) as storage:
            yield storage
        return
    async with open_checkpointer(settings) as checkpointer, open_sqlite_tables(settings) as tables:
        audit = AuditLog(tables)
        desk = TicketDesk(tables, audit)
        for table in (audit, desk):
            await table.create_table()
        yield Storage(checkpointer, HandoffQueue(tables), ConversationLocks(), audit, desk)


@asynccontextmanager
async def open_postgres(settings: Settings) -> AsyncIterator[Storage]:
    """Separate data and lock pools: a turn holds its lock connection throughout, and must never
    wait for a data connection held by a turn waiting for that lock."""
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
        tables = PostgresTables(pool)
        queue, audit = HandoffQueue(tables), AuditLog(tables)
        desk = TicketDesk(tables, audit)
        await create_tables(settings.database_url, checkpointer, queue, audit, desk)
        yield Storage(checkpointer, queue, PostgresLocks(lock_pool), audit, desk)
