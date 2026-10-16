"""The handoff queue: one row per case a human has to answer, next to the checkpoints.

The row is written before the graph pauses, so no conversation can be waiting for a human
without its case being listed. Claiming a case is a single conditional update, so two operators
resuming the same protocol cannot both win. The row also carries what the merchant wrote while
waiting, as notes for the operator, and the reply the case ended with until the merchant's next
message delivers it.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from app.contracts import CaseNote, DeliveredReply, HandoffCase, HandoffRecord, Outcome
from app.settings import Settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS handoffs (
    protocol   TEXT PRIMARY KEY,
    thread_id  TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    payload    TEXT NOT NULL,
    opened_at  TEXT NOT NULL,
    closed_at  TEXT
)
"""

_ADDED_COLUMNS = {
    "outcome": "TEXT",
    "notes": "TEXT NOT NULL DEFAULT '[]'",
    "reply": "TEXT",
    "delivered_at": "TEXT",
}
"""Columns added after the first release, created on an older database when it is opened."""

_COLUMNS = "status, payload, closed_at, outcome, notes"


class HandoffQueue:
    """Open cases waiting for an operator, and the threads they paused."""

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._db = connection

    async def open(self, case: HandoffCase, thread_id: str) -> None:
        """Record a case. Recording the same protocol twice keeps the first row."""
        await self._db.execute(
            "INSERT OR IGNORE INTO handoffs (protocol, thread_id, status, payload, opened_at) "
            "VALUES (?, ?, 'open', ?, ?)",
            (case.protocol, thread_id, case.model_dump_json(), case.opened_at.isoformat()),
        )
        await self._db.commit()

    async def get(self, protocol: str) -> tuple[HandoffRecord, str] | None:
        """A case and the thread it paused, or None when the protocol is unknown."""
        async with self._db.execute(
            f"SELECT {_COLUMNS}, thread_id FROM handoffs WHERE protocol = ?", (protocol,)
        ) as cursor:
            row = await cursor.fetchone()
        return (_record(row), row[5]) if row else None

    async def list_open(self) -> list[HandoffRecord]:
        """Every case still waiting, oldest first."""
        async with self._db.execute(
            f"SELECT {_COLUMNS} FROM handoffs WHERE status = 'open' ORDER BY opened_at"
        ) as cursor:
            return [_record(row) for row in await cursor.fetchall()]

    async def claim(self, protocol: str, outcome: Outcome) -> bool:
        """Close an open case with how it ended. False when someone else already closed it."""
        cursor = await self._db.execute(
            "UPDATE handoffs SET status = 'closed', outcome = ?, closed_at = ? "
            "WHERE protocol = ? AND status = 'open'",
            (outcome, datetime.now(UTC).isoformat(), protocol),
        )
        await self._db.commit()
        return cursor.rowcount == 1

    async def reopen(self, protocol: str) -> None:
        """Put a claimed case back when its resume did not go through."""
        await self._db.execute(
            "UPDATE handoffs SET status = 'open', outcome = NULL, closed_at = NULL "
            "WHERE protocol = ?",
            (protocol,),
        )
        await self._db.commit()

    async def add_note(self, protocol: str, message: str) -> None:
        """Append what the merchant wrote while waiting, already masked, for the operator."""
        note = CaseNote(at=datetime.now(UTC), message=message).model_dump(mode="json")
        await self._db.execute(
            "UPDATE handoffs SET notes = json_insert(notes, '$[#]', json(?)) WHERE protocol = ?",
            (json.dumps(note), protocol),
        )
        await self._db.commit()

    async def hold_reply(self, reply: DeliveredReply) -> None:
        """Keep the reply a case ended with until the merchant's next message delivers it."""
        await self._db.execute(
            "UPDATE handoffs SET reply = ?, delivered_at = NULL WHERE protocol = ?",
            (reply.model_dump_json(), reply.protocol),
        )
        await self._db.commit()

    async def deliver(self, thread_id: str) -> list[DeliveredReply]:
        """The replies this conversation has not seen yet, oldest first, marked as delivered."""
        async with self._db.execute(
            "SELECT protocol, reply FROM handoffs WHERE thread_id = ? AND reply IS NOT NULL "
            "AND delivered_at IS NULL ORDER BY closed_at",
            (thread_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        if rows:
            await self._db.executemany(
                "UPDATE handoffs SET delivered_at = ? WHERE protocol = ?",
                [(datetime.now(UTC).isoformat(), protocol) for protocol, _ in rows],
            )
            await self._db.commit()
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
        yield HandoffQueue(connection)
