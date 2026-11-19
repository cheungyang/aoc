"""One turn at a time in a household channel.

Two people typing at once in a channel they share would otherwise start two
turns on its one session, each reading and writing the same history. Here a
turn waits for the one before it (`TurnLocks.hold`), keyed by the channel, or
the thread, it runs in. The Discord runner takes the lock only for a household
agent's channel (core/permissions/access.py `is_household`): one person's
agents answer as they always did.

The locks are the process's, shared by every bot in it (`TURN_LOCKS`): the
bots all run on one event loop. A key's lock is dropped once nobody holds it
or waits for it, so there are never more than the turns in flight; and a task
that already holds a key goes straight through, since waiting for itself
would never end.
"""
import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, Optional


@dataclass
class _Key:
    """One key's lock, who holds it, and how many hold or wait for it."""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    holder: Optional[asyncio.Task] = None
    users: int = 0


class TurnLocks:
    """Locks by key, made when a turn first asks and dropped when the last one is done."""

    def __init__(self):
        self._keys: Dict[str, _Key] = {}

    @contextlib.asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        """Holds `key` for the block, after whoever holds it now. Released
        however the block ends, a cancellation included."""
        task = asyncio.current_task()
        entry = self._keys.get(key)
        if entry is not None and task is not None and entry.holder is task:
            yield  # this task holds it already
            return
        if entry is None:
            entry = self._keys[key] = _Key()
        entry.users += 1
        try:
            async with entry.lock:
                entry.holder = task
                try:
                    yield
                finally:
                    entry.holder = None
        finally:
            entry.users -= 1
            if not entry.users and self._keys.get(key) is entry:
                del self._keys[key]

    def held(self, key: str) -> bool:
        """Whether a turn holds `key` now."""
        entry = self._keys.get(key)
        return entry is not None and entry.lock.locked()

    def __len__(self) -> int:
        """How many keys a turn holds or waits for."""
        return len(self._keys)


# The process's: every bot runner shares them.
TURN_LOCKS = TurnLocks()
