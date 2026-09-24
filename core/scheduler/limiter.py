"""A cap on how many scheduled executions run at once.

Due schedules used to be fired with one `asyncio.create_task` each and nothing
in between, so every job due at the same minute hit the model at the same
instant — a burst generator pointed straight at the per-minute request quota.
This turns that burst into a queue. It costs nothing when there is no
contention and loses no work when there is: a waiting run simply starts when a
slot frees up.

The cap is the system-wide `Config().max_concurrency` (`AOC_MAX_CONCURRENCY`).
"""
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from core.util.config import Config


def configured_limit() -> int:
    return Config().max_concurrency


class ScheduleLimiter:
    def __init__(self, limit: Optional[int] = None):
        self.limit = max(1, int(limit)) if limit is not None else configured_limit()
        self._semaphore = asyncio.Semaphore(self.limit)
        self.active = 0
        self.waiting = 0
        self.peak = 0

    @asynccontextmanager
    async def slot(self, label: str = ""):
        """Holds one of the `limit` execution slots for the duration of the block."""
        if self._semaphore.locked():
            print(
                f"ScheduleLimiter: {label or 'schedule'} queued "
                f"({self.active}/{self.limit} running, {self.waiting} waiting)"
            )
        self.waiting += 1
        try:
            await self._semaphore.acquire()
        finally:
            self.waiting -= 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            yield
        finally:
            self.active -= 1
            self._semaphore.release()
