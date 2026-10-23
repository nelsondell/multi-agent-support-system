"""Append-only by trigger on both backends: who did what to which case, never message text."""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel

from app.handoffs import Tables

_TABLE = """
CREATE TABLE IF NOT EXISTS audit (
    id     TEXT PRIMARY KEY,
    at     TEXT NOT NULL,
    actor  TEXT NOT NULL,
    role   TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
)
"""

_APPEND_ONLY = {
    "sqlite": [
        "CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit "
        "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END",
        "CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit "
        "BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END",
    ],
    "postgres": [
        "CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN RAISE EXCEPTION 'audit log is append-only'; END $$",
        "CREATE OR REPLACE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit "
        "FOR EACH ROW EXECUTE FUNCTION audit_append_only()",
    ],
}


class AuditEntry(BaseModel):
    at: datetime
    actor: str
    role: str
    action: str
    target: str
    detail: str = ""


class AuditLog:
    def __init__(self, tables: Tables) -> None:
        self._db = tables

    async def create_table(self) -> None:
        await self._db.execute(_TABLE)
        for statement in _APPEND_ONLY[self._db.dialect]:
            await self._db.execute(statement)

    async def append(
        self, actor: str, role: str, action: str, target: str, detail: str = ""
    ) -> None:
        await self._db.execute(
            "INSERT INTO audit (id, at, actor, role, action, target, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid4().hex, datetime.now(UTC).isoformat(), actor, role, action, target, detail),
        )

    async def read(self, limit: int = 200) -> list[AuditEntry]:
        rows = await self._db.fetch(
            "SELECT at, actor, role, action, target, detail FROM audit ORDER BY at DESC LIMIT ?",
            (limit,),
        )
        fields = ("at", "actor", "role", "action", "target", "detail")
        return [AuditEntry(**dict(zip(fields, row, strict=True))) for row in rows]
