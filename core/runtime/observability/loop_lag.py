"""How late the event loop runs: a probe that sleeps a short, fixed interval and
records by how much each wakeup came late.

Everything in the bot's process shares one event loop (main.py), so anything
that blocks it -- a sync database call, a subprocess, a cold import -- delays
every voice and text turn at once. py-spy shows what blocked it, while it
records; this keeps a running record instead, so each voice turn's trace says
how long the loop was stalled during the turn (`loop_lag`, from `lag_between`),
and a long stall is logged as it happens.

main.py starts the probe (`watch()`) next to the bots. Nothing else does: with
no probe running, `lag_between` returns None and traces leave `loop_lag` out.
"""
import asyncio
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple

# How often the probe wakes. Each wakeup costs microseconds.
INTERVAL_S = 0.05
# A wakeup at least this late is a stall worth keeping; less is scheduling noise.
STALL_MS = 50
# A stall at least this long is logged when it ends.
LOG_MS = 500
# Stalls kept: far more than any turn has.
KEEP = 2000


class LoopLag:
    """One loop's stalls: (when it ended, time.monotonic(); how late, in ms)."""

    def __init__(self, interval: float = INTERVAL_S):
        self.interval = interval
        self.stalls: Deque[Tuple[float, float]] = deque(maxlen=KEEP)
        self.running = False

    async def watch(self) -> None:
        """Wakes every `interval` seconds until cancelled, recording each wakeup that came late."""
        self.running = True
        try:
            due = time.monotonic() + self.interval
            while True:
                await asyncio.sleep(max(0.0, due - time.monotonic()))
                now = time.monotonic()
                self.record(now, (now - due) * 1000)
                due = now + self.interval
        finally:
            self.running = False

    def record(self, at: float, late_ms: float) -> None:
        if late_ms < STALL_MS:
            return
        self.stalls.append((at, late_ms))
        if late_ms >= LOG_MS:
            print(f"[LoopLag] The event loop was blocked for {late_ms / 1000:.1f} s")

    def between(self, start: float, end: float) -> Optional[Dict[str, int]]:
        """The stalls that ended between two time.monotonic() values: how many, their
        total and the longest, in ms. None while no probe runs."""
        if not self.running:
            return None
        late = [ms for at, ms in self.stalls if start <= at <= end]
        return {"stalls": len(late), "total_ms": round(sum(late)), "max_ms": round(max(late, default=0))}


# The bot's loop: main.py runs its probe.
PROBE = LoopLag()


async def watch() -> None:
    await PROBE.watch()


def lag_between(start: float, end: float) -> Optional[Dict[str, int]]:
    return PROBE.between(start, end)
