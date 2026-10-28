import asyncio
import contextlib
import datetime
import io
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from core.scheduler import schedule_runner as runner_mod
from core.scheduler.limiter import ScheduleLimiter
from core.scheduler.schedule_runner import ScheduleRunner, reset_conversation
from core.scheduler.script_runner import ScriptResult
from core.scheduler.spec import (
    Briefing,
    Findings,
    Problems,
    PromptSchedule,
    ScriptSchedule,
    ScriptStep,
    SessionPolicy,
    WorkDecision,
)
from core.storage.schedule_state import ScheduleState
from core.util.local_time import get_local_now
from tests.core.scheduler import use_real_croniter

ALWAYS = {"type": "always", "reason": "test"}


class Gate:
    """A precondition whose answer the test controls."""

    def __init__(self, answer=True):
        self.answer = answer

    def evaluate(self, ctx):
        if isinstance(self.answer, Exception):
            raise self.answer
        return WorkDecision(bool(self.answer), "gate says so")


def prompt_entry(**kw):
    entry = {
        "kind": "prompt",
        "cron": "0 9 * * *",
        "channel": "test-channel",
        "precondition": dict(ALWAYS),
        "prompt": "test prompt",
    }
    entry.update(kw)
    return entry


class RunnerCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cron = use_real_croniter()
        cron.__enter__()
        self.addCleanup(cron.__exit__, None, None, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.state = ScheduleState(os.path.join(self._tmp.name, "memory.db"))

        self.configs = {
            "main": {"channel_hosts": ["test-channel", "general"], "schedules": []},
            "agent1": {"schedules": [prompt_entry()]},
        }
        self.loader = MagicMock()
        self.loader.list_agent_ids.side_effect = lambda: list(self.configs)
        self.loader.get_agent_config.side_effect = lambda aid: self.configs.get(aid)
        self.agent = MagicMock()
        self.agent.execute = AsyncMock(return_value="ok")
        self.loader.get_agent.return_value = self.agent

        self.channel = MagicMock()
        self.channel.send = AsyncMock()
        self.channel.threads = []
        self.bots = MagicMock()
        self.bots.get_channel.return_value = self.channel
        self.bots.find_channel.return_value = None

        self.session = MagicMock()
        self.session.session_id = "agent1:scheduled:test-channel"
        self.session.job_id = None
        self.session.turn_hints = {}

        patches = [
            patch.object(runner_mod, "AgentsLoader", return_value=self.loader),
            patch.object(runner_mod, "BotsLoader", return_value=self.bots),
            patch.object(runner_mod.SessionManager, "get_session", return_value=self.session),
            patch.object(runner_mod, "reset_conversation", return_value=0),
            patch.object(runner_mod, "record_channel_post", return_value=[]),
            patch.object(runner_mod, "record_push", return_value=None),
        ]
        self.mocks = [p.start() for p in patches]
        self.get_session = self.mocks[2]
        self.reset = self.mocks[3]
        self.record_post = self.mocks[4]
        self.record_push = self.mocks[5]
        for p in patches:
            self.addCleanup(p.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def make_runner(self, **kw):
        kw.setdefault("state", self.state)
        return ScheduleRunner(**kw)

    def prompt_spec(self, gate=True, policy=SessionPolicy.STATELESS, **kw):
        base = dict(
            schedule_id="agent1:p",
            agent_id="agent1",
            cron="* * * * *",
            channel="test-channel",
            precondition=Gate(gate),
        )
        base.update(kw)
        return PromptSchedule(prompt="do it", session_policy=policy, **base)


class TestLoading(RunnerCase):
    def test_loads_valid_schedules(self):
        runner = self.make_runner()
        self.assertEqual(len(runner.schedules), 1)
        item = runner.schedules[0]
        self.assertIsInstance(item.spec, PromptSchedule)
        self.assertEqual(item.spec.agent_id, "agent1")
        self.assertEqual(item.spec.prompt, "test prompt")
        self.assertGreater(item.next_run, get_local_now())

    def test_cron_follows_configured_timezone(self):
        from zoneinfo import ZoneInfo
        from core.config.settings import Config

        Config().timezone = "Asia/Tokyo"
        try:
            runner = self.make_runner()
        finally:
            Config().timezone = None
        self.assertEqual(runner.schedules[0].next_run.tzinfo, ZoneInfo("Asia/Tokyo"))

    def test_rejections_are_logged_and_alerted_to_their_own_channel(self):
        self.configs["agent1"]["schedules"] = [
            prompt_entry(),
            {"cron": "0 9 * * *", "prompt": "old format", "channel": "test-channel"},
            {"cron": "0 9 * * *", "prompt": "no channel"},
        ]
        runner = self.make_runner()
        self.assertEqual(len(runner.schedules), 1)
        self.assertEqual(len(runner.rejections), 2)
        # Only the rejection that names a channel is queued for an alert.
        self.assertEqual(len(runner._pending_alerts), 1)

    async def test_alert_posted_once(self):
        self.configs["agent1"]["schedules"] = [
            {"cron": "0 9 * * *", "prompt": "old format", "channel": "test-channel"},
        ]
        runner = self.make_runner()
        await runner._flush_alerts()
        self.channel.send.assert_awaited_once()
        self.assertIn("Schedule rejected", self.channel.send.call_args[0][0])
        self.assertIn("kind", self.channel.send.call_args[0][0])
        # A reload with the same bad entry does not alert again.
        runner._load_schedules()
        await runner._flush_alerts()
        self.channel.send.assert_awaited_once()

    async def test_alert_retried_until_channel_resolves_then_dropped(self):
        self.configs["agent1"]["schedules"] = [
            {"cron": "0 9 * * *", "prompt": "x", "channel": "test-channel"},
        ]
        self.bots.get_channel.return_value = None
        runner = self.make_runner()
        for _ in range(runner_mod.ALERT_ATTEMPTS):
            await runner._flush_alerts()
        self.assertEqual(runner._pending_alerts, {})

    async def test_hot_reload_keeps_next_run_for_unchanged_schedules(self):
        self.configs["agent1"]["schedules"] = [prompt_entry(id="keep")]
        runner = self.make_runner()
        sentinel = get_local_now() + datetime.timedelta(days=3)
        runner.schedules[0].next_run = sentinel

        self.configs["agent1"]["schedules"] = [prompt_entry(id="keep"), prompt_entry(id="new", cron="0 10 * * *")]
        await runner.tick(now=get_local_now())
        self.assertEqual(len(runner.schedules), 2)
        kept = [i for i in runner.schedules if i.spec.schedule_id == "agent1:keep"][0]
        self.assertEqual(kept.next_run, sentinel)


class TestTick(RunnerCase):
    async def test_due_schedule_is_fired_and_rescheduled(self):
        runner = self.make_runner()
        runner.run_schedule = AsyncMock(return_value=True)
        now = get_local_now()
        runner.schedules[0].next_run = now - datetime.timedelta(minutes=1)
        await runner.tick(now=now)
        await asyncio.sleep(0)
        runner.run_schedule.assert_awaited_once_with(runner.schedules[0].spec)
        self.assertGreater(runner.schedules[0].next_run, now)

    async def test_disabled_and_future_schedules_are_not_fired(self):
        self.configs["agent1"]["schedules"] = [prompt_entry(enabled=False)]
        runner = self.make_runner()
        runner.run_schedule = AsyncMock()
        runner.schedules[0].next_run = get_local_now() - datetime.timedelta(minutes=1)
        await runner.tick(now=get_local_now())
        await asyncio.sleep(0)
        runner.run_schedule.assert_not_called()

    async def test_start_ticks_after_each_sleep(self):
        runner = self.make_runner()
        runner.tick = AsyncMock(side_effect=[None, asyncio.CancelledError()])
        with patch.object(runner_mod, "TICK_SECONDS", 0.01):
            with self.assertRaises(asyncio.CancelledError):
                await runner.start()
        self.assertEqual(runner.tick.await_count, 2)

    async def test_a_wake_ends_the_sleep_early(self):
        runner = self.make_runner()
        ticked = asyncio.Event()

        async def tick():
            ticked.set()

        runner.tick = tick
        with patch.object(runner_mod, "TICK_SECONDS", 5):
            loop = asyncio.create_task(runner.start())
            try:
                await asyncio.sleep(0.05)
                self.assertFalse(ticked.is_set(), "still asleep")
                runner.wake()
                await asyncio.wait_for(ticked.wait(), 1.0)  # long before the 5 s sleep ends
            finally:
                loop.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await loop

    async def test_wakes_during_a_tick_make_one_more_tick(self):
        runner = self.make_runner()
        ticks = []

        async def tick():
            ticks.append(len(ticks))
            if len(ticks) == 1:
                for _ in range(5):
                    runner.wake()

        runner.tick = tick
        with patch.object(runner_mod, "TICK_SECONDS", 5):
            loop = asyncio.create_task(runner.start())
            try:
                await asyncio.sleep(0.05)
                runner.wake()
                await asyncio.sleep(0.1)
            finally:
                loop.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await loop
        self.assertEqual(ticks, [0, 1], "an Event is a flag, not a counter")

    def test_a_wake_before_start_does_nothing(self):
        self.make_runner().wake()


class SourcedSpec(PromptSchedule):
    """A spec that brings its own source and output, and says when a reply is
    silent, as a proactive delivery does (core/proactive/delivery.py)."""

    source = "proactive"

    def __init__(self, **kw):
        kw.setdefault("schedule_id", "proactive:agent1")
        kw.setdefault("agent_id", "agent1")
        kw.setdefault("cron", "")
        kw.setdefault("channel", "test-channel")
        super().__init__(prompt="tell them", **kw)
        self.outputs, self.failures = [], 0

    def output_for(self, channel):
        output = MagicMock(channel=channel)
        self.outputs.append(output)
        return output

    def is_silent(self, reply):
        return "<silent/>" in reply

    def on_failure(self, ctx):
        self.failures += 1


class TestDueWork(RunnerCase):
    async def test_due_specs_run_through_run_schedule_after_the_cron_entries(self):
        runner = self.make_runner()
        ran = []

        async def run_schedule(spec):
            ran.append(spec.schedule_id)
            return True

        runner.run_schedule = run_schedule
        now = get_local_now()
        runner.schedules[0].next_run = now - datetime.timedelta(minutes=1)
        cron = runner.schedules[0].spec.schedule_id
        given = []

        def sync_provider(at):
            given.append(at)
            return [SourcedSpec(schedule_id="proactive:a")]

        async def async_provider(at):
            return [SourcedSpec(schedule_id="proactive:b")]

        runner.add_due_work(sync_provider)
        runner.add_due_work(async_provider)
        await runner.tick(now=now)
        await asyncio.sleep(0)
        self.assertEqual(ran, [cron, "proactive:a", "proactive:b"])
        self.assertEqual(given, [now.timestamp()])

    async def test_due_specs_get_the_ownership_check(self):
        from core.channel.discord.ownership import Event, Ownership
        runner = self.make_runner()
        Ownership()._reduce(Ownership().holds, Event("claim", "agent1", "laptop"), None)
        runner.add_due_work(lambda at: [SourcedSpec()])
        await runner.tick(now=get_local_now())
        await asyncio.gather(*list(runner._tasks))
        self.agent.execute.assert_not_called()

    async def test_a_failing_provider_doesnt_stop_the_others(self):
        runner = self.make_runner()
        runner.run_schedule = AsyncMock(return_value=True)
        spec = SourcedSpec()

        def broken(at):
            raise RuntimeError("board locked")

        runner.add_due_work(broken)
        runner.add_due_work(lambda at: [spec])
        await runner.tick(now=get_local_now())
        await asyncio.sleep(0)
        runner.run_schedule.assert_awaited_once_with(spec)

    async def test_a_spec_brings_its_source_and_output(self):
        runner = self.make_runner()
        spec = SourcedSpec()
        self.assertTrue(await runner.run_schedule(spec))
        kwargs = self.get_session.call_args.kwargs
        self.assertEqual(kwargs["source"], "proactive")
        self.assertIs(kwargs["output"], spec.outputs[0])
        self.assertIs(spec.outputs[0].channel, self.channel)
        self.record_post.assert_called_once_with(self.channel, "ok", origin="proactive:agent1", loader=self.loader)

    async def test_a_silent_or_blank_reply_records_nothing(self):
        runner = self.make_runner()
        for reply, spec in (("<silent/>", SourcedSpec()), ("  \n", self.prompt_spec())):
            self.agent.execute = AsyncMock(return_value=reply)
            self.assertTrue(await runner.run_schedule(spec))
        self.record_post.assert_not_called()
        self.record_push.assert_not_called()

    async def test_a_failed_run_calls_on_failure(self):
        runner = self.make_runner()
        self.agent.execute.side_effect = RuntimeError("boom")
        spec = SourcedSpec()
        self.assertFalse(await runner.run_schedule(spec))
        self.assertEqual(spec.failures, 1)


class Briefer:
    """A briefing provider the test controls: the runs it was asked about, and how each went.
    `block` None means it has nothing for the run; `fails` is raised instead."""

    def __init__(self, block="TEAM", fails=None):
        self.block, self.fails, self.asked, self.outcomes = block, fails, [], []

    async def __call__(self, spec, ctx):
        self.asked.append((spec.schedule_id, ctx.agent_id))
        if self.fails:
            raise self.fails
        return None if self.block is None else Briefing(block=self.block, finish=self.outcomes.append)


class TestBriefings(RunnerCase):
    async def test_a_scheduled_run_gets_each_briefing_after_its_prompt(self):
        runner = self.make_runner()
        team, outcomes = Briefer("TEAM"), []
        runner.add_briefing(team)
        runner.add_briefing(lambda spec, ctx: Briefing(block="MORE", finish=outcomes.append))  # sync works too
        self.assertTrue(await runner.run_schedule(self.prompt_spec()))
        self.agent.execute.assert_awaited_once_with("do it\n\nTEAM\n\nMORE", session=self.session, role="user")
        self.assertEqual(team.asked, [("agent1:p", "agent1")])
        self.assertEqual((team.outcomes, outcomes), (["posted"], ["posted"]))

    async def test_a_blank_reply_is_silent(self):
        runner = self.make_runner()
        team = Briefer()
        runner.add_briefing(team)
        self.agent.execute = AsyncMock(return_value="  \n")
        self.assertTrue(await runner.run_schedule(self.prompt_spec()))
        self.assertEqual(team.outcomes, ["silent"])

    async def test_a_failed_job_or_an_exception_is_failed(self):
        runner = self.make_runner()
        team = Briefer()
        runner.add_briefing(team)
        self.session.job_id = "job-1"
        with patch.object(runner_mod, "JobManager") as jm:
            jm.return_value.get_job.return_value = MagicMock(status="error")
            self.assertFalse(await runner.run_schedule(self.prompt_spec()))
        self.agent.execute.side_effect = RuntimeError("boom")
        self.assertFalse(await runner.run_schedule(self.prompt_spec()))
        self.assertEqual(team.outcomes, ["failed", "failed"])

    async def test_a_proactive_run_and_a_run_with_no_channel_get_none(self):
        runner = self.make_runner()
        team = Briefer()
        runner.add_briefing(team)
        self.assertTrue(await runner.run_schedule(SourcedSpec()))
        self.assertTrue(await runner.run_schedule(self.prompt_spec(channel=None)))
        self.bots.get_channel.return_value = None  # its channel isn't found: the run posts nowhere
        self.assertTrue(await runner.run_schedule(self.prompt_spec()))
        self.assertEqual(team.asked, [])
        self.assertEqual([call.args[0] for call in self.agent.execute.await_args_list], ["tell them", "do it", "do it"])

    async def test_a_failing_provider_is_logged_and_the_run_goes_on_without_it(self):
        runner = self.make_runner()
        team = Briefer("TEAM")
        runner.add_briefing(Briefer(fails=RuntimeError("board locked")))
        runner.add_briefing(team)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertTrue(await runner.run_schedule(self.prompt_spec()))
        self.agent.execute.assert_awaited_once_with("do it\n\nTEAM", session=self.session, role="user")
        self.assertIn("board locked", err.getvalue())
        self.assertEqual(team.outcomes, ["posted"])

    async def test_a_provider_with_nothing_to_add_changes_nothing(self):
        runner = self.make_runner()
        runner.add_briefing(Briefer(block=None))
        self.assertTrue(await runner.run_schedule(self.prompt_spec()))
        self.agent.execute.assert_awaited_once_with("do it", session=self.session, role="user")

    async def test_a_finish_that_fails_is_logged(self):
        def full(outcome):
            raise RuntimeError("disk full")

        runner = self.make_runner()
        runner.add_briefing(lambda spec, ctx: Briefing(block="TEAM", finish=full))
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertTrue(await runner.run_schedule(self.prompt_spec()))
        self.assertIn("disk full", err.getvalue())


class TestPromptDispatch(RunnerCase):
    async def test_skip_costs_nothing_and_does_not_advance_success(self):
        runner = self.make_runner()
        spec = self.prompt_spec(gate=False)
        self.assertFalse(await runner.run_schedule(spec))
        self.agent.execute.assert_not_called()
        row = self.state.get(spec.schedule_id)
        self.assertEqual(row["last_skip_reason"], "gate says so")
        self.assertIsNone(row["last_success_at"])

    async def test_runs_and_records_success(self):
        runner = self.make_runner()
        spec = self.prompt_spec()
        self.assertTrue(await runner.run_schedule(spec))
        self.agent.execute.assert_awaited_once_with("do it", session=self.session, role="user")
        self.get_session.assert_called_once_with(
            agent_id="agent1", source="scheduled", channel=self.channel, thinking_level=None, output=ANY
        )
        # The run's output is its Discord channel (core/runtime/foundation/output.py).
        self.assertIs(self.get_session.call_args.kwargs["output"].channel, self.channel)
        self.assertIsNotNone(self.state.get_last_success(spec.schedule_id))

    async def test_a_scans_findings_reach_the_agent(self):
        class FindingsGate:
            def evaluate(self, ctx):
                return WorkDecision(True, "found", detail=Findings("market_screen", "CAT -6%"))

        runner = self.make_runner()
        self.assertTrue(await runner.run_schedule(self.prompt_spec(precondition=FindingsGate())))
        self.agent.execute.assert_awaited_once_with(
            'do it\n\n<findings source="market_screen">\nCAT -6%\n</findings>', session=self.session, role="user"
        )

    async def test_a_scans_problems_are_posted_once_per_issue_per_day(self):
        issues = ["pkm/wallet/real-estate/criteria.md: names no state"]

        class ProblemGate:
            def evaluate(self, ctx):
                return WorkDecision(False, "can't run", detail=Problems("property_screen", tuple(issues)))

        runner = self.make_runner()
        spec = self.prompt_spec(precondition=ProblemGate(), entry={"id": "weekly-scout"})
        self.assertFalse(await runner.run_schedule(spec))
        self.agent.execute.assert_not_called()
        self.channel.send.assert_awaited_once()
        posted = self.channel.send.call_args[0][0]
        self.assertEqual(posted, (
            "⚠️ The scheduled check `weekly-scout` couldn't run normally:\n"
            "- pkm/wallet/real-estate/criteria.md: names no state\n"
            "Reply here and I'll help fix it."
        ))
        self.record_post.assert_called_once_with(self.channel, posted, origin=spec.schedule_id, loader=self.loader)
        # The notice enters the channel inbox too, named after the schedule's label.
        self.assertEqual(self.record_push.call_args.args, (self.channel, posted))
        self.assertEqual(self.record_push.call_args.kwargs["label"], "a problem report")

        # The same issue later that day is not posted again, even after a restart; a new one is.
        await self.make_runner().run_schedule(spec)
        self.channel.send.assert_awaited_once()
        issues.append("pkm/wallet/real-estate/criteria.md: max price unreadable")
        await runner.run_schedule(spec)
        self.assertEqual(self.channel.send.await_count, 2)
        self.assertNotIn("names no state", self.channel.send.call_args[0][0])
        self.assertIn("max price unreadable", self.channel.send.call_args[0][0])

    async def test_problems_without_a_channel_are_logged_and_not_claimed(self):
        class ProblemGate:
            def evaluate(self, ctx):
                return WorkDecision(False, "can't run", detail=Problems("ha_audit", ("x: broken",)))

        self.bots.get_channel.return_value = None
        runner = self.make_runner()
        spec = self.prompt_spec(precondition=ProblemGate())
        await runner.run_schedule(spec)
        self.channel.send.assert_not_called()
        self.assertTrue(self.state.claim_notice(spec.schedule_id, "x: broken", get_local_now().strftime("%Y-%m-%d")))

    async def test_a_notice_that_fails_to_post_is_tried_again_next_run(self):
        class ProblemGate:
            def evaluate(self, ctx):
                return WorkDecision(False, "can't run", detail=Problems("ha_audit", ("x: broken",)))

        runner = self.make_runner()
        spec = self.prompt_spec(precondition=ProblemGate())
        self.channel.send.side_effect = [RuntimeError("discord down"), None]
        await runner.run_schedule(spec)
        self.record_post.assert_not_called()
        await runner.run_schedule(spec)
        self.assertEqual(self.channel.send.await_count, 2)
        self.record_post.assert_called_once()

    async def test_a_quiet_skip_posts_nothing(self):
        runner = self.make_runner()
        self.assertFalse(await runner.run_schedule(self.prompt_spec(gate=False)))
        self.channel.send.assert_not_called()
        self.record_post.assert_not_called()

    async def test_schedule_thinking_level_reaches_the_session(self):
        runner = self.make_runner()
        self.assertTrue(await runner.run_schedule(self.prompt_spec(thinking_level="low")))
        self.assertEqual(self.get_session.call_args[1]["thinking_level"], "low")

    async def test_failed_job_does_not_record_success(self):
        runner = self.make_runner()
        self.session.job_id = "job-1"
        job = MagicMock(status="error")
        with patch.object(runner_mod, "JobManager") as jm:
            jm.return_value.get_job.return_value = job
            self.assertFalse(await runner.run_schedule(self.prompt_spec()))
        row = self.state.get("agent1:p")
        self.assertIsNone(row["last_success_at"])
        self.assertIsNotNone(row["last_run_at"])

    async def test_exception_in_execute_is_a_failure(self):
        runner = self.make_runner()
        self.agent.execute.side_effect = RuntimeError("boom")
        self.assertFalse(await runner.run_schedule(self.prompt_spec()))
        self.assertIsNone(self.state.get_last_success("agent1:p"))

    async def test_stateless_resets_history_persistent_does_not(self):
        runner = self.make_runner()
        await runner.run_schedule(self.prompt_spec(policy=SessionPolicy.STATELESS))
        self.reset.assert_called_once_with(self.session)
        self.reset.reset_mock()
        await runner.run_schedule(self.prompt_spec(policy=SessionPolicy.PERSISTENT))
        self.reset.assert_not_called()

    async def test_has_work_crash_fails_open(self):
        runner = self.make_runner()
        self.assertTrue(await runner.run_schedule(self.prompt_spec(gate=ValueError("broken"))))
        self.agent.execute.assert_awaited_once()

    async def test_skips_schedules_of_agents_held_by_dev(self):
        from core.channel.discord.ownership import Event, Ownership
        runner = self.make_runner()
        Ownership()._reduce(Ownership().holds, Event("claim", "agent1", "laptop"), None)
        self.assertFalse(await runner.run_schedule(self.prompt_spec()))
        self.agent.execute.assert_not_called()

    async def test_thread_is_resolved(self):
        thread = MagicMock()
        thread.name = "t1"
        self.channel.threads = [thread]
        runner = self.make_runner()
        await runner.run_schedule(self.prompt_spec(thread="t1"))
        self.assertIs(self.get_session.call_args[1]["channel"], thread)

    async def test_channel_owner_lookup(self):
        runner = self.make_runner()
        await runner.run_schedule(self.prompt_spec())
        self.bots.get_channel.assert_called_with("main", "test-channel")

    async def test_limiter_caps_concurrent_runs(self):
        limiter = ScheduleLimiter(1)
        runner = self.make_runner(limiter=limiter)

        async def slow(*a, **k):
            await asyncio.sleep(0.01)

        self.agent.execute.side_effect = slow
        await asyncio.gather(*(runner.run_schedule(self.prompt_spec(schedule_id=f"agent1:{i}")) for i in range(3)))
        self.assertEqual(limiter.peak, 1)
        self.assertEqual(self.agent.execute.await_count, 3)

    async def test_successful_reply_is_recorded_for_the_channel(self):
        runner = self.make_runner()
        await runner.run_schedule(self.prompt_spec())
        self.record_post.assert_called_once_with(self.channel, "ok", origin="agent1:p", loader=self.loader)

    async def test_failed_run_is_not_recorded(self):
        runner = self.make_runner()
        self.session.job_id = "job-1"
        with patch.object(runner_mod, "JobManager") as jm:
            jm.return_value.get_job.return_value = MagicMock(status="error")
            await runner.run_schedule(self.prompt_spec())
        self.record_post.assert_not_called()
        self.record_push.assert_not_called()

    def _execute_with(self, response):
        """The agent's run leaves `response` where Agent.execute does, and
        notes which agent the scheduled speech scope named."""
        from core.runtime.foundation.events import RESPONSE_HINT
        from core.prompts.voice import _scheduled_speech_agent
        seen = {}

        async def execute(prompt, session=None, role="user"):
            seen["scope"] = _scheduled_speech_agent.get()
            if response is not None:
                session.turn_hints[RESPONSE_HINT] = response
            return "ok"

        self.agent.execute = AsyncMock(side_effect=execute)
        return seen

    async def test_a_post_enters_the_inbox_with_the_runs_speech_version(self):
        from core.channel.response_parser import AgentResponse
        from core.storage.channel_inbox import Priority

        seen = self._execute_with(AgentResponse(
            text="ok", speech="Daisy here. How's your energy, one to ten?", question="How's your energy, one to ten?"))
        await self.make_runner().run_schedule(self.prompt_spec(priority="DEFAULT", label="your morning check-in"))

        self.assertEqual(seen["scope"], "agent1", "the run gets the scheduled <voice_mode> block")
        self.record_push.assert_called_once_with(
            self.channel, "ok", schedule_id="agent1:p", agent_id="agent1", priority=Priority.DEFAULT,
            label="your morning check-in", speech="Daisy here. How's your energy, one to ten?",
            question="How's your energy, one to ten?", loader=self.loader,
        )

    async def test_an_ignore_schedule_writes_no_speech_version(self):
        from core.storage.channel_inbox import Priority

        seen = self._execute_with(None)
        await self.make_runner().run_schedule(self.prompt_spec(priority="IGNORE"))

        self.assertIsNone(seen["scope"])
        self.assertIs(self.record_push.call_args.kwargs["priority"], Priority.IGNORE)  # which makes no row
        self.assertIsNone(self.record_push.call_args.kwargs["speech"])

    async def test_nothing_recorded_when_channel_unresolved(self):
        self.bots.get_channel.return_value = None
        runner = self.make_runner()
        await runner.run_schedule(self.prompt_spec())
        self.agent.execute.assert_awaited_once()
        self.record_post.assert_not_called()


class TestScriptDispatch(RunnerCase):
    def script_spec(self, steps):
        return ScriptSchedule(
            steps=steps,
            schedule_id="script-executor:s",
            agent_id="script-executor",
            cron="* * * * *",
            channel="test-channel",
        )

    async def test_runs_only_steps_with_work_and_posts_output(self):
        a, b = ScriptStep("a.py"), ScriptStep("b.py")
        spec = self.script_spec([a, b])
        answers = {"a.py": WorkDecision(False, "idle"), "b.py": WorkDecision(True, "busy")}
        runner = self.make_runner()
        with patch("core.scheduler.script_runner.call_has_work",
                   side_effect=lambda step, ctx, scripts_dir=None: answers[step.script]), \
             patch.object(runner_mod, "run_step", AsyncMock(return_value=ScriptResult("b.py", True, "did b"))) as run:
            self.assertTrue(await runner.run_schedule(spec))
        run.assert_awaited_once()
        self.assertEqual(run.call_args[0][0], b)
        self.channel.send.assert_awaited_once_with("did b")
        self.record_post.assert_called_once_with(self.channel, "did b", origin="script-executor:s", loader=self.loader)
        # Its speech version is written only if you join voice while it's unfinished.
        self.record_push.assert_called_once_with(
            self.channel, "did b", schedule_id="script-executor:s", agent_id="script-executor",
            priority=spec.priority, label=None, loader=self.loader,
        )
        self.agent.execute.assert_not_called()
        self.get_session.assert_not_called()

    async def test_empty_output_posts_nothing(self):
        spec = self.script_spec([ScriptStep("a.py")])
        runner = self.make_runner()
        with patch("core.scheduler.script_runner.call_has_work", return_value=WorkDecision(True, "")), \
             patch.object(runner_mod, "run_step", AsyncMock(return_value=ScriptResult("a.py", True, ""))):
            self.assertTrue(await runner.run_schedule(spec))
        self.channel.send.assert_not_called()
        self.record_post.assert_not_called()

    async def test_failed_step_fails_the_run(self):
        spec = self.script_spec([ScriptStep("a.py")])
        runner = self.make_runner()
        with patch("core.scheduler.script_runner.call_has_work", return_value=WorkDecision(True, "")), \
             patch.object(runner_mod, "run_step", AsyncMock(return_value=ScriptResult("a.py", False, "Error executing script 'a.py': x"))):
            self.assertFalse(await runner.run_schedule(spec))
        self.channel.send.assert_awaited_once()
        self.assertIsNone(self.state.get_last_success(spec.schedule_id))

    async def test_no_step_with_work_is_a_skip(self):
        spec = self.script_spec([ScriptStep("a.py")])
        runner = self.make_runner()
        with patch("core.scheduler.script_runner.call_has_work", return_value=WorkDecision(False, "idle")), \
             patch.object(runner_mod, "run_step", AsyncMock()) as run:
            self.assertFalse(await runner.run_schedule(spec))
        run.assert_not_called()

    async def test_forwards_schedule_timeout_to_run_step(self):
        step = ScriptStep("a.py")
        spec = ScriptSchedule(
            steps=[step],
            timeout=1800,
            schedule_id="script-executor:s",
            agent_id="script-executor",
            cron="* * * * *",
            channel="test-channel",
        )
        runner = self.make_runner()
        with patch("core.scheduler.script_runner.call_has_work", return_value=WorkDecision(True, "ready")), \
             patch.object(runner_mod, "run_step", AsyncMock(return_value=ScriptResult("a.py", True, ""))) as run:
            self.assertTrue(await runner.run_schedule(spec))
        run.assert_awaited_once_with(step, scripts_dir=None, timeout=1800)


class TestResetConversation(unittest.TestCase):
    def test_deletes_checkpoints_only(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "memory.db")
            table = runner_mod.sanitize_table_name("agent1:scheduled:c")
            # A hand-built table with only the columns the reset needs: it must not be given the schema.
            conn = sqlite3.connect(db)
            conn.execute(f'CREATE TABLE "{table}" (entry_type TEXT, data TEXT)')
            conn.executemany(
                f'INSERT INTO "{table}" VALUES (?, ?)',
                [("checkpoint", "1"), ("write", "2"), ("token_usage", "3"), ("message", "4")],
            )
            conn.commit()
            conn.close()

            session = MagicMock()
            session.get_session_thread_id.return_value = "agent1:scheduled:c"
            with patch.dict(os.environ, {"MEMORY_DB_PATH": db}):  # where SessionStore() finds memory.db
                self.assertEqual(reset_conversation(session), 2)
                session.get_session_thread_id.return_value = "missing:thread"
                self.assertEqual(reset_conversation(session), 0)

            conn = sqlite3.connect(db)
            left = sorted(r[0] for r in conn.execute(f'SELECT entry_type FROM "{table}"'))
            columns = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
            conn.close()
            self.assertEqual(left, ["message", "token_usage"])
            self.assertEqual(columns, ["entry_type", "data"], "not migrated")
            self.assertEqual(tables, [table], "no table made for the missing thread")


if __name__ == "__main__":
    unittest.main()
