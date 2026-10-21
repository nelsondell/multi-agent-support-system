"""The handoff queue: one row per case a human has to answer, next to the checkpoints.

The row is written before the graph pauses, so no conversation can be waiting for a human
without its case being listed. Claiming a case is a single conditional update, so two operators
resuming the same protocol cannot both win. The row also carries what the merchant wrote while
waiting, as notes for the operator, and the reply the case ended with until the merchant's next
message delivers it.

The same SQL runs on SQLite, one node's file and the default, and on Postgres, shared by every
replica: each database is a two-method adapter, `execute` and `fetch`.
"""

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import aiosqlite

from app.contracts import CaseNote, DeliveredReply, HandoffCase, HandoffRecord, Outcome
from app.settings import Settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS handoffs (
    protocol     TEXT PRIMARY KEY,
    thread_id    TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    payload      TEXT NOT NULL,
    opened_at    TEXT NOT NULL,
    closed_at    TEXT,
    outcome      TEXT,
    notes        TEXT NOT NULL DEFAULT '[]',
    reply        TEXT,
    delivered_at TEXT
)
"""

_ADDED_COLUMNS = {
    "outcome": "TEXT",
    "notes": "TEXT NOT NULL DEFAULT '[]'",
    "reply": "TEXT",
    "delivered_at": "TEXT",
}
"""Columns added after the first release, created on an older SQLite database when it is
opened. Postgres arrived later and starts from the full schema."""

_COLUMNS = "status, payload, closed_at, outcome, notes"


class Tables(Protocol):
    """A database as the queue uses it. Parameters are written `?`; every write commits."""

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a statement and return how many rows it changed."""
        ...

    async def fetch(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        """Run a query and return its rows as tuples."""
        ...


class SqliteTables:
    """The queue's SQL on one SQLite connection."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._db = connection

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        cursor = await self._db.execute(sql, params)
        await self._db.commit()
        return cursor.rowcount

    async def fetch(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        async with self._db.execute(sql, params) as cursor:
            return [tuple(row) for row in await cursor.fetchall()]


class PostgresTables:
    """The same SQL on a Postgres connection pool, in autocommit."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(sql.replace("?", "%s"), params)
            return cursor.rowcount

    async def fetch(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        from psycopg.rows import tuple_row

        async with (
            self._pool.connection() as connection,
            connection.cursor(row_factory=tuple_row) as cursor,
        ):
            await cursor.execute(sql.replace("?", "%s"), params)
            return await cursor.fetchall()


class HandoffQueue:
    """Open cases waiting for an operator, and the threads they paused."""

    def __init__(self, tables: Tables) -> None:
        self._db = tables

    async def create_table(self) -> None:
        """The table, on a database that has none yet."""
        await self._db.execute(_SCHEMA)

    async def ping(self) -> None:
        """Raise unless the database answers; what `/ready` asks."""
        await self._db.fetch("SELECT 1")

    async def open(self, case: HandoffCase, thread_id: str) -> None:
        """Record a case. Recording the same protocol twice keeps the first row."""
        await self._db.execute(
            "INSERT INTO handoffs (protocol, thread_id, status, payload, opened_at) "
            "VALUES (?, ?, 'open', ?, ?) ON CONFLICT (protocol) DO NOTHING",
            (case.protocol, thread_id, case.model_dump_json(), case.opened_at.isoformat()),
        )

    async def get(self, protocol: str) -> tuple[HandoffRecord, str] | None:
        """A case and the thread it paused, or None when the protocol is unknown."""
        rows = await self._db.fetch(
            f"SELECT {_COLUMNS}, thread_id FROM handoffs WHERE protocol = ?", (protocol,)
        )
        return (_record(rows[0]), rows[0][5]) if rows else None

    async def list_open(self) -> list[HandoffRecord]:
        """Every case still waiting, oldest first."""
        rows = await self._db.fetch(
            f"SELECT {_COLUMNS} FROM handoffs WHERE status = 'open' ORDER BY opened_at"
        )
        return [_record(row) for row in rows]

    async def claim(self, protocol: str, outcome: Outcome) -> bool:
        """Close an open case with how it ended. False when someone else already closed it."""
        changed = await self._db.execute(
            "UPDATE handoffs SET status = 'closed', outcome = ?, closed_at = ? "
            "WHERE protocol = ? AND status = 'open'",
            (outcome, datetime.now(UTC).isoformat(), protocol),
        )
        return changed == 1

    async def reopen(self, protocol: str) -> None:
        """Put a claimed case back when its resume did not go through."""
        await self._db.execute(
            "UPDATE handoffs SET status = 'open', outcome = NULL, closed_at = NULL "
            "WHERE protocol = ?",
            (protocol,),
        )

    async def add_note(self, protocol: str, message: str) -> None:
        """Append what the merchant wrote while waiting, already masked, for the operator. Called
        under the conversation's lock, so two notes on one case never interleave."""
        note = CaseNote(at=datetime.now(UTC), message=message).model_dump(mode="json")
        rows = await self._db.fetch("SELECT notes FROM handoffs WHERE protocol = ?", (protocol,))
        if rows:
            notes = json.dumps([*json.loads(rows[0][0] or "[]"), note])
            await self._db.execute(
                "UPDATE handoffs SET notes = ? WHERE protocol = ?", (notes, protocol)
            )

    async def hold_reply(self, reply: DeliveredReply) -> None:
        """Keep the reply a case ended with until the merchant's next message delivers it."""
        await self._db.execute(
            "UPDATE handoffs SET reply = ?, delivered_at = NULL WHERE protocol = ?",
            (reply.model_dump_json(), reply.protocol),
        )

    async def deliver(self, thread_id: str) -> list[DeliveredReply]:
        """The replies this conversation has not seen yet, oldest first, marked as delivered."""
        rows = await self._db.fetch(
            "SELECT protocol, reply FROM handoffs WHERE thread_id = ? AND reply IS NOT NULL "
            "AND delivered_at IS NULL ORDER BY closed_at",
            (thread_id,),
        )
        delivered_at = datetime.now(UTC).isoformat()
        for protocol, _ in rows:
            await self._db.execute(
                "UPDATE handoffs SET delivered_at = ? WHERE protocol = ?", (delivered_at, protocol)
            )
        return [DeliveredReply.model_validate_json(reply) for _, reply in rows]


def _record(row: tuple) -> HandoffRecord:
    status, payload, closed_at, outcome, notes = row[:5]
    return HandoffRecord(
        status=status,
        outcome=outcome,
        case=HandoffCase.model_validate_json(payload),
        closed_at=datetime.fromisoformat(closed_at) if closed_at else None,
        notes=json.loads(notes or "[]"),
    )


async def _migrate(connection: aiosqlite.Connection) -> None:
    """Create the table, and add the columns a database from an earlier release lacks."""
    await connection.execute(_SCHEMA)
    async with connection.execute("PRAGMA table_info(handoffs)") as cursor:
        present = {row[1] for row in await cursor.fetchall()}
    for column, declaration in _ADDED_COLUMNS.items():
        if column not in present:
            await connection.execute(f"ALTER TABLE handoffs ADD COLUMN {column} {declaration}")
    await connection.commit()


@asynccontextmanager
async def open_handoff_queue(settings: Settings) -> AsyncIterator[HandoffQueue]:
    """Open the queue database under `STATE_DIR`, creating it on first use."""
    state_dir = Path(settings.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(state_dir / "handoffs.sqlite")) as connection:
        await _migrate(connection)
        yield HandoffQueue(SqliteTables(connection))
