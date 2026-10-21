"""The one write the agents can make: a technical ticket for a terminal that stays offline.

The mock backend is a table next to the cases. Opening is idempotent on the proposal id, so a
second confirmation of one proposal returns the first ticket, and the ticket is a single insert,
so a failure leaves none behind. Every ticket opened and every proposal cancelled is audited.
"""

from datetime import UTC, datetime

from pydantic import BaseModel

from app.audit import AuditLog
from app.handoffs import Tables

_TABLE = """
CREATE TABLE IF NOT EXISTS tickets (
    proposal    TEXT PRIMARY KEY,
    id          TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    terminal_id TEXT NOT NULL,
    symptom     TEXT NOT NULL,
    opened_at   TEXT NOT NULL
)
"""


class Ticket(BaseModel):
    id: str
    terminal_id: str
    symptom: str


class TicketDesk:
    """Open a ticket once per proposal, and find it again by proposal and merchant."""

    def __init__(self, tables: Tables, audit: AuditLog) -> None:
        self._db, self._audit = tables, audit

    async def create_table(self) -> None:
        await self._db.execute(_TABLE)

    async def open(self, proposal: str, user_id: str, terminal_id: str, symptom: str) -> Ticket:
        """The ticket for this proposal: opened by the first call, returned by every later one."""
        changed = await self._db.execute(
            "INSERT INTO tickets (proposal, id, user_id, terminal_id, symptom, opened_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (proposal) DO NOTHING",
            (
                proposal,
                f"TKT-{proposal[:8].upper()}",
                user_id,
                terminal_id,
                symptom,
                datetime.now(UTC).isoformat(),
            ),
        )
        ticket = await self.find(proposal, user_id)
        if changed:
            await self._audit.append(user_id, "merchant", "ticket.opened", ticket.id, terminal_id)
        return ticket

    async def find(self, proposal: str, user_id: str) -> Ticket | None:
        """This merchant's ticket for this proposal; another merchant's is None."""
        rows = await self._db.fetch(
            "SELECT id, terminal_id, symptom FROM tickets WHERE proposal = ? AND user_id = ?",
            (proposal, user_id),
        )
        return Ticket(id=rows[0][0], terminal_id=rows[0][1], symptom=rows[0][2]) if rows else None

    async def cancelled(self, proposal: str, user_id: str) -> None:
        await self._audit.append(user_id, "merchant", "action.cancelled", proposal)
