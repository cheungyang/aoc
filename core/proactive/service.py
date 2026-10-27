"""The proactive team's background work in the bot process.

`turn_ended` is the taps' sink (taps.SignalHook). It returns at once. The
entry is written to the Board on its worker thread (board.py), and then the
session's Switchboard run is scheduled by the entry's tap:

  effect     at once: a receipt saved, food logged, a plan made
  scheduled  at once: a scheduled run just posted
  chat       after QUIET_S of quiet in that session, and at most MAX_WAIT_S
             after its first unread turn: a conversation is read as a whole

A run reads every unread turn of the session (SignalStore.pending_turns) and
hands them to `decide`, the Switchboard and Governor pipeline
(pipeline.py). At most CONCURRENT_RUNS run at once. Turns still unread after a
restart are picked up by `start()`.
"""
import asyncio
import time
from typing import Awaitable, Callable, Dict, List, Optional, Set

from core.channel.discord.radio import DiscordRadio, on_poll_vote, on_reaction
from core.proactive.board import on_board
from core.proactive.pipeline import Pipeline
from core.proactive.radio import TeamRadio
from core.proactive.taps import TAP_CHAT, CallLog, LedgerEntry, SignalHook
from core.runtime.hooks import set_turn_hooks, turn_hooks
from core.storage.signals import SignalStore
from core.tooling.hooks import set_tool_hooks, tool_hooks

# How long a conversation must go quiet before the Switchboard reads it, and
# how long it waits at most after the conversation's first unread turn.
QUIET_S = 90.0
MAX_WAIT_S = 600.0
# Switchboard runs at once, across sessions: bounds the cost of a burst.
CONCURRENT_RUNS = 2
# After a restart, how long before the turns left unread are picked up: the
# bots are connecting meanwhile.
CATCH_UP_S = 30.0

# How often the would-deliver times of `today` and `next` routes are resolved.
SWEEP_S = 60.0

Decide = Callable[[str, List[dict]], Awaitable[None]]
Sweep = Callable[[], Awaitable[None]]


def install(decide: Optional[Decide] = None, sweep: Optional[Sweep] = None) -> "ProactiveService":
    """Taps every agent turn into a new ProactiveService: a tool hook notes each
    finished tool call (taps.CallLog), and a turn hook hands each finished turn
    to the service (taps.SignalHook). main.py calls it after bootstrap.wire(),
    so only the bot process taps: scripts and tests that wire the runtime don't.
    `decide` defaults to the Switchboard pipeline (pipeline.py), which tells
    team radio (radio.py) about each decision. Nothing is delivered to an
    agent yet (stage S4): a route records when it would have been.
    Returns the service, whose `start()` runs next to the bots."""
    if decide is None:
        radio = TeamRadio(DiscordRadio())
        on_reaction(radio.reacted)
        on_poll_vote(radio.voted)
        pipeline = Pipeline(radio=radio)
        decide, sweep = pipeline, pipeline.resolve
    service = ProactiveService(decide=decide, sweep=sweep)
    set_tool_hooks(list(tool_hooks()) + [CallLog()])
    set_turn_hooks(list(turn_hooks()) + [SignalHook(service.turn_ended)])
    print("[proactive] Tapping agent turns.")
    return service


class ProactiveService:
    """Records tapped turns and runs `decide` on them when their tap says so."""

    def __init__(self, decide: Optional[Decide] = None, store: Optional[SignalStore] = None,
                 clock: Callable[[], float] = time.time, sweep: Optional[Sweep] = None):
        self.decide = decide
        self.sweep = sweep
        self.store = store or SignalStore()
        self.clock = clock
        self._timers: Dict[str, asyncio.TimerHandle] = {}
        self._first_unread: Dict[str, float] = {}
        self._tasks: Set[asyncio.Task] = set()
        self._runs: Optional[asyncio.Semaphore] = None

    # -- the taps' sink ----------------------------------------------------------

    def turn_ended(self, entry: LedgerEntry) -> None:
        """Records `entry` and schedules its session's Switchboard run, in the background."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop: a generator closed during shutdown
        self._spawn(loop, self._record(entry))

    async def _record(self, entry: LedgerEntry) -> None:
        try:
            await on_board(self.store.record_turn, entry.row())
        except Exception as e:
            print(f"[proactive] Could not record {entry.agent_id}'s turn: {type(e).__name__}: {e}")
            return
        self.schedule(entry.session_id, entry.tap, entry.at)

    # -- scheduling ------------------------------------------------------------------

    def schedule(self, session_id: str, tap: str, at: Optional[float] = None) -> None:
        """Sets when `session_id`'s unread turns are read: at once, or after the quiet (module docstring)."""
        if self.decide is None:
            return
        loop = asyncio.get_running_loop()
        now = self.clock()
        first = self._first_unread.setdefault(session_id, at if at is not None else now)
        delay = 0.0
        if tap == TAP_CHAT:
            delay = max(0.0, min(QUIET_S, first + MAX_WAIT_S - now))
        timer = self._timers.pop(session_id, None)
        if timer is not None:
            timer.cancel()
        self._timers[session_id] = loop.call_later(delay, self._due, session_id)

    def _due(self, session_id: str) -> None:
        self._timers.pop(session_id, None)
        self._first_unread.pop(session_id, None)
        self._spawn(asyncio.get_running_loop(), self.run(session_id))

    async def run(self, session_id: str) -> None:
        """Reads `session_id`'s unread turns and hands them to `decide`."""
        if self._runs is None:
            self._runs = asyncio.Semaphore(CONCURRENT_RUNS)
        async with self._runs:
            try:
                turns = await on_board(self.store.pending_turns, session_id)
                if turns:
                    await self.decide(session_id, turns)
            except Exception as e:
                print(f"[proactive] The Switchboard run for {session_id} failed: {type(e).__name__}: {e}")

    async def start(self) -> None:
        """Runs next to the bots: picks up the turns a restart left unread, then
        sweeps every SWEEP_S, if there is a sweep."""
        await asyncio.sleep(CATCH_UP_S)
        try:
            turns = await on_board(self.store.pending_turns)
        except Exception as e:
            print(f"[proactive] Could not read the unread turns: {type(e).__name__}: {e}")
            turns = []
        for session_id in dict.fromkeys(t["session_id"] for t in turns):
            if session_id not in self._timers:
                self.schedule(session_id, "catch-up")
        while self.sweep is not None:
            await asyncio.sleep(SWEEP_S)
            try:
                await self.sweep()
            except Exception as e:
                print(f"[proactive] The sweep failed: {type(e).__name__}: {e}")

    def _spawn(self, loop: asyncio.AbstractEventLoop, coro) -> None:
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
