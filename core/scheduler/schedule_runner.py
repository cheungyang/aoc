"""The scheduler loop: fires due schedules through the ScheduleSpec protocol.

For every due schedule the runner:

1. asks `has_work()` — cheap, no model. A "no" is logged and costs nothing;
   when a scan says why it couldn't decide, that is posted in the schedule's
   channel, once per issue per day (`_post_problems`);
2. takes a slot from the concurrency limiter, so simultaneous schedules queue
   instead of bursting the model's per-minute quota;
3. dispatches by kind — a prompt becomes an agent turn, a script a subprocess;
4. records `last_success_at` only if the run succeeded. A run that failed
   calls the spec's `on_failure`.

One loop, two alarm clocks. The loop sleeps TICK_SECONDS between ticks, and
`wake()` ends a sleep early, for work that shouldn't wait up to 30 s (a
proactive delivery approved for `now`). A wake starts nothing itself: it only
brings the next tick forward, so there is still one loop and one tick at a
time. Each tick fires the due cron entries, then the due work: every provider
registered with `add_due_work` returns the specs due now, and each runs as a
task through `run_schedule`, with the same ownership check and limiter as a
schedule. A provider claims its own items, so none runs twice
(core/proactive/delivery.py claims each delivery with one conditional UPDATE,
and core/proactive/follow_ups.py each follow-up's due check).
Such a spec may bring its own source and output (`source`, `output_for`), and
say when its reply posted nothing (`is_silent`): then nothing is recorded.

A scheduled run that posts somewhere can also be briefed: each provider
registered with `add_briefing` may add a block after its prompt (the team's
findings for that agent, core/proactive/delivery.py), and then hears how the
run went: posted, silent or failed. Only source "scheduled" is briefed: never a
proactive delivery or a follow-up's check.

Every post it makes also puts its channel in the channel inbox (core/voice/briefing),
unless the schedule's priority is IGNORE, so a voice briefing can tell you
about it. A prompt run that isn't IGNORE also writes the speech version of
its post (the scheduled <voice_mode> block), stored with that row.

Schedules that fail validation are never registered. Each rejection is logged
and posted to the channel named in that schedule's own entry (log only if it
has none). Schedules are re-read whenever an `agent.json` schedules array
changes, so an edit takes effect without a restart.
"""
import asyncio
import contextlib
import datetime
import inspect
import json
import sys
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Union

from croniter import croniter

from core.channel.discord.loader import BotsLoader
from core.channel.discord.output import DiscordOutput
from core.loaders.agents_loader import AgentsLoader
from core.runtime.foundation import events
from core.runtime.history.channel_posts import record_channel_post
from core.runtime.foundation.jobs import JobManager
from core.runtime.foundation.sessions import SessionManager
from core.scheduler.limiter import ScheduleLimiter
from core.scheduler.registry import Rejection, load_all
from core.scheduler.script_runner import run_step
from core.scheduler.spec import (
    RUN_FAILED,
    RUN_POSTED,
    RUN_SILENT,
    Briefing,
    Problems,
    PromptSchedule,
    ScheduleContext,
    ScheduleSpec,
    ScriptSchedule,
    SessionPolicy,
    WorkDecision,
)
from core.storage.schedule_state import ScheduleState
from core.storage.sessions import CHECKPOINT_ENTRIES, SessionStore, sanitize_table_name
from core.channel.inbox import record_push
from core.storage.channel_inbox import Priority
from core.channel.chunks import safe_chunks
from core.prompts.voice import scheduled_speech_scope
from core.channel.discord.ownership import PROD, Ownership
from core.util.local_time import get_local_now

TICK_SECONDS = 30
# Job statuses that mean the scheduled turn did not do its work.
FAILED_JOB_STATUSES = {"error", "killing", "killed", "timeout"}
# Bots connect after the runner starts; an alert whose channel cannot be
# resolved yet is retried on later ticks before being given up (and logged).
ALERT_ATTEMPTS = 10

# A due-work provider (`add_due_work`): given the epoch time, the specs due now,
# claimed, or an awaitable of them.
DueWork = Callable[[float], Union[Iterable[ScheduleSpec], Awaitable[Iterable[ScheduleSpec]]]]
# A briefing provider (`add_briefing`): given a scheduled run's spec and context,
# a Briefing for it or None, or an awaitable of either.
BriefingProvider = Callable[[ScheduleSpec, ScheduleContext], Union[Optional[Briefing], Awaitable[Optional[Briefing]]]]


def reset_conversation(session) -> int:
    """Empties a scheduled session's conversation history before a STATELESS run.

    The scheduled session keeps its one table (`ctx_<agent>_scheduled_<channel>`)
    — no new table per run — but its checkpoints, which are what LangGraph
    replays as history, are deleted. Token-usage and message-log rows stay, so
    accounting is untouched. Returns the number of rows removed.
    """
    table = sanitize_table_name(session.get_session_thread_id())
    return SessionStore().delete_entries([table], CHECKPOINT_ENTRIES)


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
        self._due_work: List[DueWork] = []
        self._briefing_providers: List[BriefingProvider] = []
        self._wake: Optional[asyncio.Event] = None  # made by start(), on the running loop
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
        """Ticks every TICK_SECONDS, or sooner when woken (module docstring)."""
        print("ScheduleRunner started.")
        self._wake = asyncio.Event()
        while True:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=TICK_SECONDS)
            self._wake.clear()
            await self.tick()

    def wake(self) -> None:
        """Brings the next tick forward; call it on the event loop. It starts nothing
        itself, and any number of wakes during a tick make one more tick, not one each.
        A no-op before start()."""
        if self._wake is not None:
            self._wake.set()

    def add_due_work(self, provider: DueWork) -> None:
        """Registers `provider(now)`: each tick, after the cron entries, it's given the
        epoch time and returns the specs due now. It claims them itself: the runner
        runs each spec it returns, once, through run_schedule."""
        self._due_work.append(provider)

    def add_briefing(self, provider: BriefingProvider) -> None:
        """Registers `provider(spec, ctx)`: before each run of a spec whose source is
        "scheduled" and whose channel was found, it may return a Briefing. Its block
        goes after the run's prompt, and its finish then hears how the run went:
        posted when the reply was recorded as a post, silent when the run succeeded
        but its reply was blank or silent, failed otherwise."""
        self._briefing_providers.append(provider)

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
                self._spawn(self.run_schedule(item.spec))
                triggered = True
                # Update next run time
                try:
                    item.next_run = self._next_run(item.spec.cron, now)
                except Exception as e:
                    print(f"Error updating next run for {item.spec.agent_id}: {e}")
        await self._run_due_work(now.timestamp())

    async def _run_due_work(self, now: float) -> None:
        """The specs each provider says are due now, each run as a task through run_schedule."""
        for provider in list(self._due_work):
            try:
                specs = provider(now)
                if inspect.isawaitable(specs):
                    specs = await specs
            except Exception as e:
                print(f"ScheduleRunner: due work failed: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            for spec in specs or ():
                self._spawn(self.run_schedule(spec))

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        if hasattr(task, "add_done_callback"):
            task.add_done_callback(self._tasks.discard)

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
        ownership = Ownership()
        if not await ownership.wait_ready() and ownership.role == PROD:
            # Discord unreachable: prod keeps running its schedules rather than
            # silently dropping them; a dev holding the agent is the rare case.
            print(f"ScheduleRunner: claims unknown (control thread not read); running {spec.describe()} on prod.")
        elif not ownership.is_mine(spec.agent_id):
            holder = ownership.holder(spec.agent_id) or "prod"
            print(f"ScheduleRunner: Skipping {spec.describe()} on {ownership.label()}; {spec.agent_id} is held by {holder}.")
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

        # Off the event loop: a scan may make network calls (a Zillow job can poll for
        # minutes), and the loop also serves Discord's heartbeat and voice.
        decision = await asyncio.to_thread(self._decide, spec, ctx)
        if not decision:
            print(f"ScheduleRunner: skipping {spec.describe()}: {decision.reason}")
            try:
                spec.on_skip(ctx, decision.reason)
            except Exception as e:
                print(f"ScheduleRunner: on_skip failed for {spec.schedule_id}: {e}")
            self._record(self.state.record_skip, spec.schedule_id, decision.reason)
            if isinstance(decision.detail, Problems):
                await self._post_problems(spec, decision.detail)
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
            try:
                spec.on_failure(ctx)
            except Exception as e:
                print(f"ScheduleRunner: on_failure failed for {spec.schedule_id}: {e}")
            self._record(self.state.record_failure, spec.schedule_id, started)
        return ok

    async def _dispatch(self, spec: ScheduleSpec, ctx: ScheduleContext, decision: WorkDecision) -> bool:
        if isinstance(spec, ScriptSchedule):
            steps = decision.detail if isinstance(decision.detail, list) and decision.detail else spec.steps
            return await self._run_script(spec, ctx, steps)
        if isinstance(spec, PromptSchedule):
            return await self._run_prompt(spec, ctx, decision)
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

    async def _run_prompt(self, spec: PromptSchedule, ctx: ScheduleContext, decision: Optional[WorkDecision] = None) -> bool:
        channel = self._resolve_channel(spec.agent_id, spec.channel, spec.thread)
        output = None
        if channel is not None:
            # A spec may write its own way: a proactive delivery posts nothing for a silent reply.
            output_for = getattr(spec, "output_for", None)
            output = output_for(channel) if callable(output_for) else DiscordOutput(channel)

        # Execute regardless of channel existence
        source = getattr(spec, "source", "scheduled")
        session = SessionManager.get_session(
            agent_id=spec.agent_id,
            source=source,
            channel=channel or spec.channel,
            thinking_level=spec.thinking_level,
            output=output,
        )
        if spec.session_policy is SessionPolicy.STATELESS:
            try:
                removed = reset_conversation(session)
                if removed:
                    print(f"ScheduleRunner: cleared {removed} checkpoint row(s) from {session.session_id} (stateless run)")
            except Exception as e:
                print(f"ScheduleRunner: could not reset {session.session_id} for a stateless run: {e}")

        agent = self.loader.get_agent(spec.agent_id)
        prompt = spec.build_prompt(ctx, decision)
        # Only a scheduled run with somewhere to post is briefed (`add_briefing`).
        briefings = await self._briefings_for(spec, ctx) if source == "scheduled" and channel is not None else []
        prompt = "\n\n".join([prompt] + [briefing.block for briefing in briefings if briefing.block])
        outcome = RUN_FAILED  # unless the run gets further, or raises
        try:
            if spec.priority is Priority.IGNORE:
                reply = await agent.execute(prompt, session=session, role="user")
            else:
                # The post may be heard later, so the run also writes its speech version.
                with scheduled_speech_scope(spec.agent_id):
                    reply = await agent.execute(prompt, session=session, role="user")
            ok = self._job_succeeded(getattr(session, "job_id", None))
            # Only a successful run's reply was posted as the schedule's output (a
            # failure posts an error message instead), and only if there was a
            # channel to post it to. A reply that says nothing posted nothing.
            if ok and channel is not None and isinstance(reply, str) and not self._silent(spec, reply):
                record_channel_post(channel, reply, origin=spec.schedule_id, loader=self.loader)
                response = session.turn_hints.get(events.RESPONSE_HINT)
                record_push(
                    channel, reply, schedule_id=spec.schedule_id, agent_id=spec.agent_id,
                    priority=spec.priority, label=spec.label,
                    speech=getattr(response, "speech", None), question=getattr(response, "question", None),
                    loader=self.loader,
                )
                outcome = RUN_POSTED
            elif ok:
                outcome = RUN_SILENT
            return ok
        finally:
            await self._finish_briefings(briefings, outcome)

    async def _briefings_for(self, spec: PromptSchedule, ctx: ScheduleContext) -> List[Briefing]:
        """What each briefing provider adds to this run. One that fails is logged, and the
        run goes on without it."""
        briefings = []
        for provider in list(self._briefing_providers):
            try:
                briefing = provider(spec, ctx)
                if inspect.isawaitable(briefing):
                    briefing = await briefing
            except Exception as e:
                print(f"ScheduleRunner: a briefing for {spec.describe()} failed: {type(e).__name__}: {e}",
                      file=sys.stderr)
                continue
            if briefing is not None:
                briefings.append(briefing)
        return briefings

    @staticmethod
    async def _finish_briefings(briefings: List[Briefing], outcome: str) -> None:
        """Tells each briefing how its run went. One whose finish fails is logged."""
        for briefing in briefings:
            try:
                finished = briefing.finish(outcome)
                if inspect.isawaitable(finished):
                    await finished
            except Exception as e:
                print(f"ScheduleRunner: a briefing could not finish ({outcome}): {type(e).__name__}: {e}",
                      file=sys.stderr)

    @staticmethod
    def _silent(spec: ScheduleSpec, reply: str) -> bool:
        """A blank reply, or one the spec calls silent (`is_silent`)."""
        is_silent = getattr(spec, "is_silent", None)
        return not reply.strip() or (callable(is_silent) and bool(is_silent(reply)))

    async def _run_script(self, spec: ScriptSchedule, ctx: ScheduleContext, steps) -> bool:
        results = []
        for step in steps:
            results.append(await run_step(step, scripts_dir=spec.scripts_dir, timeout=spec.timeout))

        output = "\n".join(r.output for r in results if r.output and r.output.strip())
        # Nothing to say: post nothing. An empty tick must not surface at all.
        if output.strip():
            channel = self._resolve_channel(spec.agent_id, spec.channel, spec.thread)
            if channel is not None:
                for chunk in safe_chunks(output):
                    await channel.send(chunk)
                record_channel_post(channel, output, origin=spec.schedule_id, loader=self.loader)
                # No speech version yet: it's written if you join voice while it's unfinished.
                record_push(channel, output, schedule_id=spec.schedule_id, agent_id=spec.agent_id,
                            priority=spec.priority, label=spec.label, loader=self.loader)
            else:
                print(f"[{spec.agent_id}] {output}")
        return all(r.ok for r in results)

    # ---------------------------------------------------------------- alerts

    async def _flush_alerts(self):
        """Posts pending rejection alerts to each rejected schedule's own channel."""
        for key, pending in list(self._pending_alerts.items()):
            rejection, attempts = pending
            ownership = Ownership()
            if not ownership.is_ready():
                continue  # retry on the next flush, once claims are known
            if not ownership.is_mine(rejection.agent_id):
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

    async def _post_problems(self, spec: ScheduleSpec, problems: Problems) -> None:
        """Posts what a scan couldn't read in the schedule's channel, once per issue per day.

        A scan that skipped quietly over a file that changed shape would leave the
        user believing there was nothing to report. The schedule state remembers
        what was posted, so a restart doesn't repeat it, and the post is recorded
        as a channel post, so the agent sees it when the user replies to fix it.
        """
        try:
            channel = self._resolve_channel(spec.agent_id, spec.channel, spec.thread)
        except Exception as e:
            print(f"ScheduleRunner: could not resolve #{spec.channel} for {spec.schedule_id}'s problems: {e}")
            channel = None
        if channel is None:
            print(f"ScheduleRunner: {spec.schedule_id} problems (no channel to post to): {'; '.join(problems.issues)}")
            return

        day = get_local_now().strftime("%Y-%m-%d")
        fresh = []
        for issue in problems.issues:
            try:
                if self.state.claim_notice(spec.schedule_id, issue, day):
                    fresh.append(issue)
            except Exception as e:  # better a repeated notice than none
                print(f"ScheduleRunner: could not record a notice for {spec.schedule_id}: {e}")
                fresh.append(issue)
        if not fresh:
            return

        name = spec.entry.get("id") or problems.source
        message = Problems(problems.source, tuple(fresh), raw=problems.raw).message(f"The scheduled check `{name}`")
        try:
            for chunk in safe_chunks(message):
                await channel.send(chunk)
        except Exception as e:
            print(f"ScheduleRunner: failed to post problems for {spec.schedule_id}: {e}")
            for issue in fresh:  # not posted, so the next run may try again
                self._record(self.state.release_notice, spec.schedule_id, issue, day)
            return
        record_channel_post(channel, message, origin=spec.schedule_id, loader=self.loader)
        record_push(channel, message, schedule_id=spec.schedule_id, agent_id=spec.agent_id,
                    priority=spec.priority, label=f"a problem with {spec.label}" if spec.label else "a problem report",
                    loader=self.loader)
