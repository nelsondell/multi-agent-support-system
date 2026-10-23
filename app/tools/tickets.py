"""The one write: a technical ticket in a mock backend table, audited, once per proposal."""

from datetime import UTC, datetime

from pydantic import BaseModel

from app.audit import AuditLog
from app.handoffs import Tables
from app.observability.otel import operation

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
    def __init__(self, tables: Tables, audit: AuditLog) -> None:
        self._db, self._audit = tables, audit

    async def create_table(self) -> None:
        await self._db.execute(_TABLE)

    async def open(self, proposal: str, user_id: str, terminal_id: str, symptom: str) -> Ticket:
        """A single idempotent insert: a replay returns the first ticket, a failure leaves none."""
        row = (proposal, f"TKT-{proposal[:8].upper()}", user_id, terminal_id, symptom)
        tool = {"gen_ai.tool.name": "open_technical_ticket"}
        with operation("execute_tool", "open_technical_ticket", **tool):
            changed = await self._db.execute(
                "INSERT INTO tickets (proposal, id, user_id, terminal_id, symptom, opened_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (proposal) DO NOTHING",
                (*row, datetime.now(UTC).isoformat()),
            )
        ticket = await self.find(proposal, user_id)
        if changed:
            await self._audit.append(user_id, "merchant", "ticket.opened", ticket.id, terminal_id)
        return ticket

    async def find(self, proposal: str, user_id: str) -> Ticket | None:
        rows = await self._db.fetch(
            "SELECT id, terminal_id, symptom FROM tickets WHERE proposal = ? AND user_id = ?",
            (proposal, user_id),
        )
        return Ticket(id=rows[0][0], terminal_id=rows[0][1], symptom=rows[0][2]) if rows else None

    async def cancelled(self, proposal: str, user_id: str) -> None:
        await self._audit.append(user_id, "merchant", "action.cancelled", proposal)
