"""`make purge`: erase conversations idle past RETENTION_DAYS. With SQLite, run it while the API is
idle: the turn locks it takes are this process's own."""

import asyncio
from datetime import UTC, datetime, timedelta

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.settings import Settings, get_settings
from app.storage import open_storage


async def idle_threads(checkpointer: BaseCheckpointSaver, cutoff: datetime) -> list[str]:
    """The threads whose newest checkpoint is older than `cutoff`."""
    newest: dict[str, datetime] = {}
    async for item in checkpointer.alist(None):
        thread = item.config["configurable"]["thread_id"]
        written = datetime.fromisoformat(item.checkpoint["ts"])
        newest[thread] = max(newest.get(thread, written), written)
    return [thread for thread, written in newest.items() if written < cutoff]


async def purge(settings: Settings, now: datetime | None = None) -> int:
    """Erase every idle conversation; return how many."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=settings.retention_days)
    async with open_storage(settings) as storage:
        threads = await idle_threads(storage.checkpointer, cutoff)
        for thread in threads:
            async with storage.locks.hold(thread):
                await storage.checkpointer.adelete_thread(thread)
                await storage.queue.erase(thread)
        detail = f"{len(threads)} idle for over {settings.retention_days} days"
        await storage.audit.append("system", "system", "retention.purged", "*", detail)
    return len(threads)


if __name__ == "__main__":
    settings = get_settings()
    erased = asyncio.run(purge(settings))
    print(f"erased {erased} conversations idle for over {settings.retention_days} days")
