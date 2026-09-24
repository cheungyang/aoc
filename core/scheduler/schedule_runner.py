"""The scheduler loop: fires due schedules through the ScheduleSpec protocol.

For every due schedule the runner:

1. asks `has_work()` — cheap, no model. A "no" is logged and costs nothing;
2. takes a slot from the concurrency limiter, so simultaneous schedules queue
   instead of bursting the model's per-minute quota;
3. dispatches by kind — a prompt becomes an agent turn, a script a subprocess;
4. records `last_success_at` only if the run succeeded.

Schedules that fail validation are never registered. Each rejection is logged
and posted to the channel named in that schedule's own entry (log only if it
has none). Schedules are re-read whenever an `agent.json` schedules array
changes, so an edit takes effect without a restart.
"""
import asyncio
import datetime
import json
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from croniter import croniter

from core.channel.discord.loader import BotsLoader
from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer, sanitize_table_name
from core.loaders.agents_loader import AgentsLoader
from core.runtime.job_manager import JobManager
from core.runtime.session_manager import SessionManager
from core.scheduler.limiter import ScheduleLimiter
from core.scheduler.registry import Rejection, load_all
from core.scheduler.script_runner import run_step
from core.scheduler.spec import (
    PromptSchedule,
    ScheduleContext,
    ScheduleSpec,
    ScriptSchedule,
    SessionPolicy,
    WorkDecision,
)
from core.scheduler.state import ScheduleState
from core.util import split_message
from core.util.config import Config
from core.util.time_util import get_local_now

TICK_SECONDS = 30
# Job statuses that mean the scheduled turn did not do its work.
FAILED_JOB_STATUSES = {"error", "killing", "killed", "timeout"}
# Bots connect after the runner starts; an alert whose channel cannot be
# resolved yet is retried on later ticks before being given up (and logged).
ALERT_ATTEMPTS = 10


def reset_conversation(session) -> int:
    """Empties a scheduled session's conversation history before a STATELESS run.

    The scheduled session keeps its one table (`ctx_<agent>_scheduled_<channel>`)
    — no new table per run — but its checkpoints, which are what LangGraph
    replays as history, are deleted. Token-usage and message-log rows stay, so
    accounting is untouched. Returns the number of rows removed.
    """
    table = sanitize_table_name(session.get_session_thread_id())
    conn = sqlite3.connect(SqliteCheckpointer().db_path)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)
        ).fetchone()
        if not exists:
            return 0
        cursor = conn.execute(
            f"DELETE FROM \"{table}\" WHERE entry_type IN ('checkpoint', 'write')"
        )
        conn.commit()
        return cursor.rowcount or 0
    finally:
        conn.close()


@dataclass
class ScheduledItem:
    spec: ScheduleSpec
    next_run: datetime.datetime


class ScheduleRunner:
    def __init__(
        self,
        limiter: Optional[ScheduleLimiter] = None,
        state: Optional[ScheduleState] = None,
    ):
        self.loader = AgentsLoader()
        self.bots_loader = BotsLoader()
        self.limiter = limiter or ScheduleLimiter()
        self.state = state or ScheduleState()
        self.schedules: List[ScheduledItem] = []
        self.rejections: List[Rejection] = []
        self._pending_alerts: Dict[str, List[Any]] = {}
        self._alerted = set()
        self._fingerprint: Optional[str] = None
        self._tasks = set()
        self._load_schedules()

    # ------------------------------------------------------------------ load

    def _schedules_fingerprint(self) -> str:
        data = {
            agent_id: (self.loader.get_agent_config(agent_id) or {}).get("schedules")
            for agent_id in sorted(self.loader.list_agent_ids())
        }
        return json.dumps(data, sort_keys=True, default=str)

    @staticmethod
    def _next_run(cron_expr: str, now: datetime.datetime) -> datetime.datetime:
        return croniter(cron_expr, now).get_next(datetime.datetime)

    def _load_schedules(self):
        previous = {item.spec.schedule_id: item.next_run for item in self.schedules}
        # Cron expressions are authored as the user's wall clock. The container
        # runs on UTC, so evaluating them against a naive now() fired every job
        # 7-8 hours early. croniter keeps the tzinfo it is handed, so passing an
        # aware local datetime also makes the schedules follow DST.
        now = get_local_now()

        result = load_all(self.loader)
        specs: List[ScheduleSpec] = list(result.specs)

        items = []
        for spec in specs:
            if spec.schedule_id in previous:
                items.append(ScheduledItem(spec, previous[spec.schedule_id]))
                continue
            try:
                items.append(ScheduledItem(spec, self._next_run(spec.cron, now)))
            except Exception as e:
                print(f"Error parsing cron '{spec.cron}' for agent {spec.agent_id}: {e}")

        self.schedules = items
        self.rejections = list(result.rejections)
        for rejection in self.rejections:
            print(
                f"ScheduleRunner: REJECTED {rejection.agent_id} schedules[{rejection.index}] "
                f"(cron {rejection.cron!r}): {rejection.reason}",
                file=sys.stderr,
            )
            key = rejection.key()
            if key in self._alerted or key in self._pending_alerts:
                continue
            if rejection.channel:
                self._pending_alerts[key] = [rejection, 0]
            else:
                # No channel in the entry: there is nowhere it would have posted,
                # so there is nowhere to alert. The log line above is the record.
                self._alerted.add(key)

        try:
            self._fingerprint = self._schedules_fingerprint()
        except Exception:
            self._fingerprint = None
        print(f"Loaded {len(items)} schedules ({len(self.rejections)} rejected).")

    def _maybe_reload(self):
        try:
            fingerprint = self._schedules_fingerprint()
        except Exception as e:
            print(f"ScheduleRunner: could not read schedules for reload: {e}")
            return
        if fingerprint != self._fingerprint:
            print("ScheduleRunner: agent.json schedules changed; reloading.")
            self._load_schedules()

    # ------------------------------------------------------------------ loop

    async def start(self):
        print("ScheduleRunner started.")
        while True:
            await asyncio.sleep(TICK_SECONDS)
            await self.tick()

    async def tick(self, now: Optional[datetime.datetime] = None):
        self._maybe_reload()
        await self._flush_alerts()

        now = now or get_local_now()
        triggered = False
        for item in self.schedules:
            if not item.spec.enabled:
                continue
            if now >= item.next_run:
                if triggered:
                    await asyncio.sleep(2)
                task = asyncio.create_task(self.run_schedule(item.spec))
                self._tasks.add(task)
                if hasattr(task, "add_done_callback"):
                    task.add_done_callback(self._tasks.discard)
                triggered = True
                # Update next run time
                try:
                    item.next_run = self._next_run(item.spec.cron, now)
                except Exception as e:
                    print(f"Error updating next run for {item.spec.agent_id}: {e}")

    # ------------------------------------------------------------- execution

    def _last_success(self, spec: ScheduleSpec) -> Optional[float]:
        try:
            return self.state.get_last_success(spec.schedule_id)
        except Exception as e:
            print(f"ScheduleRunner: could not read state for {spec.schedule_id}: {e}")
            return None

    def _record(self, method, *args):
        try:
            method(*args)
        except Exception as e:
            print(f"ScheduleRunner: could not record schedule state: {e}")

    @staticmethod
    def _decide(spec: ScheduleSpec, ctx: ScheduleContext) -> WorkDecision:
        try:
            return WorkDecision.coerce(spec.has_work(ctx))
        except Exception as e:
            # Fail open: a broken check must not silently stop wanted work.
            print(
                f"ScheduleRunner: has_work() failed for {spec.describe()} "
                f"({type(e).__name__}: {e}); running anyway.",
                file=sys.stderr,
            )
            return WorkDecision(True, f"has_work() failed, running anyway: {e}")

    async def run_schedule(self, spec: ScheduleSpec) -> bool:
        """Gate, queue, dispatch, record. Returns True if the run succeeded."""
        if not Config().is_channel_allowed(spec.channel):
            print(f"ScheduleRunner: Skipping schedule for {spec.agent_id} on channel '{spec.channel}' (debug mode active, restricted to '{Config().debug_channel}')")
            return False

        started = time.time()
        ctx = ScheduleContext(
            schedule_id=spec.schedule_id,
            agent_id=spec.agent_id,
            now=started,
            last_success_at=self._last_success(spec),
            channel=spec.channel,
            thread=spec.thread,
            entry=spec.entry,
        )

        decision = self._decide(spec, ctx)
        if not decision:
            print(f"ScheduleRunner: skipping {spec.describe()}: {decision.reason}")
            try:
                spec.on_skip(ctx, decision.reason)
            except Exception as e:
                print(f"ScheduleRunner: on_skip failed for {spec.schedule_id}: {e}")
            self._record(self.state.record_skip, spec.schedule_id, decision.reason)
            return False

        async with self.limiter.slot(spec.describe()):
            print(f"Triggering schedule {spec.describe()}" + (f" thread {spec.thread}" if spec.thread else "") + f": {decision.reason}")
            try:
                ok = await self._dispatch(spec, ctx, decision)
            except Exception as e:
                print(f"Error executing schedule for {spec.agent_id}: {e}")
                ok = False

        if ok:
            try:
                spec.on_success(ctx)
            except Exception as e:
                print(f"ScheduleRunner: on_success failed for {spec.schedule_id}: {e}")
            self._record(self.state.record_success, spec.schedule_id, started)
        else:
            self._record(self.state.record_failure, spec.schedule_id, started)
        return ok

    async def _dispatch(self, spec: ScheduleSpec, ctx: ScheduleContext, decision: WorkDecision) -> bool:
        if isinstance(spec, ScriptSchedule):
            steps = decision.detail if isinstance(decision.detail, list) and decision.detail else spec.steps
            return await self._run_script(spec, ctx, steps)
        if isinstance(spec, PromptSchedule):
            return await self._run_prompt(spec, ctx)
        raise TypeError(f"Unknown schedule type: {type(spec).__name__}")

    def _resolve_channel(self, agent_id: str, channel_name: Optional[str], thread_name: Optional[str] = None):
        """The Discord channel (or thread) object for a schedule, or None."""
        if not channel_name:
            return None

        # Find which agent owns the channel
        owner_agent_id = None
        for aid in self.loader.list_agent_ids():
            config = self.loader.get_agent_config(aid) or {}
            if channel_name in (config.get("channel_hosts") or []):
                owner_agent_id = aid
                break
        channel = self.bots_loader.get_channel(owner_agent_id, channel_name) if owner_agent_id else None
        if channel is None:
            channel = self.bots_loader.find_channel(channel_name)

        if channel is None:
            print(f"Channel {channel_name} not found for agent {agent_id}")
            return None

        if thread_name:
            found_thread = None
            # channel.threads is a list of active threads
            for thread in getattr(channel, "threads", None) or []:
                if thread.name == thread_name or str(thread.id) == thread_name:
                    found_thread = thread
                    break

            if found_thread:
                print(f"Using thread {found_thread.name} ({found_thread.id})")
                return found_thread
            print(f"Thread {thread_name} not found in channel {channel_name}, falling back to channel.")
        return channel

    @staticmethod
    def _job_succeeded(job_id: Optional[str]) -> bool:
        """Agent.execute() reports failures in-band, so the job row is the verdict."""
        if not job_id:
            return True
        try:
            job = JobManager().get_job(job_id)
        except Exception as e:
            print(f"ScheduleRunner: could not read job {job_id}: {e}")
            return True
        return job is None or job.status not in FAILED_JOB_STATUSES

    async def _run_prompt(self, spec: PromptSchedule, ctx: ScheduleContext) -> bool:
        channel = self._resolve_channel(spec.agent_id, spec.channel, spec.thread)

        # Execute regardless of channel existence
        session = SessionManager.get_session(
            agent_id=spec.agent_id,
            source="scheduled",
            channel=channel or spec.channel,
        )
        if spec.session_policy is SessionPolicy.STATELESS:
            try:
                removed = reset_conversation(session)
                if removed:
                    print(f"ScheduleRunner: cleared {removed} checkpoint row(s) from {session.session_id} (stateless run)")
            except Exception as e:
                print(f"ScheduleRunner: could not reset {session.session_id} for a stateless run: {e}")

        agent = self.loader.get_agent(spec.agent_id)
        await agent.execute(spec.build_prompt(ctx), session=session, role="user")
        return self._job_succeeded(getattr(session, "job_id", None))

    async def _run_script(self, spec: ScriptSchedule, ctx: ScheduleContext, steps) -> bool:
        results = []
        for step in steps:
            results.append(await run_step(step, scripts_dir=spec.scripts_dir))

        output = "\n".join(r.output for r in results if r.output and r.output.strip())
        # Nothing to say: post nothing. An empty tick must not surface at all.
        if output.strip():
            channel = self._resolve_channel(spec.agent_id, spec.channel, spec.thread)
            if channel is not None:
                for chunk in split_message(output):
                    await channel.send(chunk)
            else:
                print(f"[{spec.agent_id}] {output}")
        return all(r.ok for r in results)

    # ---------------------------------------------------------------- alerts

    async def _flush_alerts(self):
        """Posts pending rejection alerts to each rejected schedule's own channel."""
        for key, pending in list(self._pending_alerts.items()):
            rejection, attempts = pending
            if not Config().is_channel_allowed(rejection.channel):
                del self._pending_alerts[key]
                self._alerted.add(key)
                continue
            try:
                channel = self._resolve_channel(rejection.agent_id, rejection.channel)
            except Exception as e:
                print(f"ScheduleRunner: could not resolve #{rejection.channel} for an alert: {e}")
                channel = None

            if channel is None:
                pending[1] = attempts + 1
                if pending[1] >= ALERT_ATTEMPTS:
                    print(f"ScheduleRunner: gave up posting rejection alert to #{rejection.channel}: {rejection.message()}")
                    del self._pending_alerts[key]
                    self._alerted.add(key)
                continue

            try:
                await channel.send(rejection.message())
            except Exception as e:
                print(f"ScheduleRunner: failed to post rejection alert to #{rejection.channel}: {e}")
            del self._pending_alerts[key]
            self._alerted.add(key)
