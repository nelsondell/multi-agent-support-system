"""The handoff queue: idempotent recording, and a claim only one resume can win."""

from datetime import UTC, datetime

from app.contracts import EscalationReason, HandoffCase
from app.handoffs import open_handoff_queue
from app.settings import Settings


def case(protocol: str = "ESC-1A2B3C4D5E", hour: int = 12) -> HandoffCase:
    return HandoffCase(
        protocol=protocol,
        reason=EscalationReason.INDIVIDUAL_CASE,
        summary="charged twice for one sale",
        user_id="cliente1988",
        conversation_id="c1",
        opened_at=datetime(2026, 9, 1, hour, tzinfo=UTC),
    )


async def test_recording_a_case_twice_keeps_one_row(tmp_path):
    async with open_handoff_queue(Settings(state_dir=str(tmp_path))) as queue:
        await queue.open(case(), "thread-1")
        await queue.open(case(), "thread-2")

        assert [record.case.protocol for record in await queue.list_open()] == ["ESC-1A2B3C4D5E"]
        assert (await queue.get("ESC-1A2B3C4D5E"))[1] == "thread-1"


async def test_only_the_first_claim_wins_and_a_reopen_undoes_it(tmp_path):
    async with open_handoff_queue(Settings(state_dir=str(tmp_path))) as queue:
        await queue.open(case(), "thread-1")

        assert await queue.claim("ESC-1A2B3C4D5E", "answered") is True
        assert await queue.claim("ESC-1A2B3C4D5E", "answered") is False
        assert await queue.list_open() == []

        await queue.reopen("ESC-1A2B3C4D5E")
        assert len(await queue.list_open()) == 1


async def test_cases_survive_reopening_the_database(tmp_path):
    settings = Settings(state_dir=str(tmp_path))
    async with open_handoff_queue(settings) as queue:
        await queue.open(case(), "thread-1")

    async with open_handoff_queue(settings) as queue:
        record, thread = await queue.get("ESC-1A2B3C4D5E")
        unknown = await queue.get("ESC-0000000000")

    assert record.status == "open" and thread == "thread-1"
    assert unknown is None


async def test_open_cases_are_listed_oldest_first(tmp_path):
    async with open_handoff_queue(Settings(state_dir=str(tmp_path))) as queue:
        await queue.open(case("ESC-BBBBBBBBBB", hour=15), "thread-2")
        await queue.open(case("ESC-AAAAAAAAAA", hour=9), "thread-1")

        listed = [record.case.protocol for record in await queue.list_open()]

    assert listed == ["ESC-AAAAAAAAAA", "ESC-BBBBBBBBBB"]


async def test_a_queue_from_an_earlier_release_gains_its_new_columns(tmp_path):
    """The state volume outlives upgrades: an older table is extended, not replaced."""
    import aiosqlite

    async with aiosqlite.connect(str(tmp_path / "handoffs.sqlite")) as old:
        await old.execute(
            "CREATE TABLE handoffs (protocol TEXT PRIMARY KEY, thread_id TEXT NOT NULL, "
            "status TEXT NOT NULL CHECK (status IN ('open', 'closed')), payload TEXT NOT NULL, "
            "opened_at TEXT NOT NULL, closed_at TEXT)"
        )
        await old.execute(
            "INSERT INTO handoffs (protocol, thread_id, status, payload, opened_at) "
            "VALUES (?, 'thread-1', 'open', ?, ?)",
            ("ESC-1A2B3C4D5E", case().model_dump_json(exclude={"expires_at"}), "2026-09-01"),
        )
        await old.commit()

    async with open_handoff_queue(Settings(state_dir=str(tmp_path))) as queue:
        await queue.add_note("ESC-1A2B3C4D5E", "ainda esperando")
        record, _ = await queue.get("ESC-1A2B3C4D5E")

    assert record.status == "open" and record.case.expires_at is None
    assert [note.message for note in record.notes] == ["ainda esperando"]
