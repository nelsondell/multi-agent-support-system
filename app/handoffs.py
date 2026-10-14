"""The handoff queue: one row per case a human has to answer, next to the checkpoints.

The row is written before the graph pauses, so no conversation can be waiting for a human
without its case being listed. Claiming a case is a single conditional update, so two operators
resuming the same protocol cannot both win.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from app.contracts import HandoffCase, HandoffRecord
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
            "SELECT status, payload, closed_at, thread_id FROM handoffs WHERE protocol = ?",
            (protocol,),
        ) as cursor:
            row = await cursor.fetchone()
        return (_record(row), row[3]) if row else None

    async def list_open(self) -> list[HandoffRecord]:
        """Every case still waiting, oldest first."""
        async with self._db.execute(
            "SELECT status, payload, closed_at FROM handoffs WHERE status = 'open' "
            "ORDER BY opened_at"
        ) as cursor:
            return [_record(row) for row in await cursor.fetchall()]

    async def claim(self, protocol: str) -> bool:
        """Close an open case. False when someone else already closed it."""
        cursor = await self._db.execute(
            "UPDATE handoffs SET status = 'closed', closed_at = ? "
            "WHERE protocol = ? AND status = 'open'",
            (datetime.now(UTC).isoformat(), protocol),
        )
        await self._db.commit()
        return cursor.rowcount == 1

    async def reopen(self, protocol: str) -> None:
        """Put a claimed case back when its resume did not go through."""
        await self._db.execute(
            "UPDATE handoffs SET status = 'open', closed_at = NULL WHERE protocol = ?",
            (protocol,),
        )
        await self._db.commit()


def _record(row: tuple) -> HandoffRecord:
    status, payload, closed_at = row[:3]
    return HandoffRecord(
        status=status,
        case=HandoffCase.model_validate_json(payload),
        closed_at=datetime.fromisoformat(closed_at) if closed_at else None,
    )


@asynccontextmanager
async def open_handoff_queue(settings: Settings) -> AsyncIterator[HandoffQueue]:
    """Open the queue database under `STATE_DIR`, creating it on first use."""
    state_dir = Path(settings.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(state_dir / "handoffs.sqlite")) as connection:
        await connection.execute(_SCHEMA)
        await connection.commit()
        yield HandoffQueue(connection)
