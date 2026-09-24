import asyncio
import datetime
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.scheduler import schedule_runner as runner_mod
from core.scheduler.limiter import ScheduleLimiter
from core.scheduler.schedule_runner import ScheduleRunner, reset_conversation
from core.scheduler.script_runner import ScriptResult
from core.scheduler.spec import (
    PromptSchedule,
    ScriptSchedule,
    ScriptStep,
    SessionPolicy,
    WorkDecision,
)
from core.scheduler.state import ScheduleState
from core.util.time_util import get_local_now
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

        patches = [
            patch.object(runner_mod, "AgentsLoader", return_value=self.loader),
            patch.object(runner_mod, "BotsLoader", return_value=self.bots),
            patch.object(runner_mod.SessionManager, "get_session", return_value=self.session),
            patch.object(runner_mod, "reset_conversation", return_value=0),
        ]
        self.mocks = [p.start() for p in patches]
        self.get_session = self.mocks[2]
        self.reset = self.mocks[3]
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
        from core.util.config import Config

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

    async def test_start_loops_on_tick(self):
        runner = self.make_runner()
        runner.tick = AsyncMock()
        with patch.object(runner_mod.asyncio, "sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])):
            with self.assertRaises(asyncio.CancelledError):
                await runner.start()
        runner.tick.assert_awaited_once()


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
        self.get_session.assert_called_once_with(agent_id="agent1", source="scheduled", channel=self.channel)
        self.assertIsNotNone(self.state.get_last_success(spec.schedule_id))

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

    async def test_debug_mode_skips_unallowed_channels(self):
        runner = self.make_runner()
        with patch.object(runner_mod, "Config") as config:
            config.return_value.is_channel_allowed.return_value = False
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
        self.agent.execute.assert_not_called()
        self.get_session.assert_not_called()

    async def test_empty_output_posts_nothing(self):
        spec = self.script_spec([ScriptStep("a.py")])
        runner = self.make_runner()
        with patch("core.scheduler.script_runner.call_has_work", return_value=WorkDecision(True, "")), \
             patch.object(runner_mod, "run_step", AsyncMock(return_value=ScriptResult("a.py", True, ""))):
            self.assertTrue(await runner.run_schedule(spec))
        self.channel.send.assert_not_called()

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


class TestResetConversation(unittest.TestCase):
    def test_deletes_checkpoints_only(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "memory.db")
            table = runner_mod.sanitize_table_name("agent1:scheduled:c")
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
            with patch.object(runner_mod, "SqliteCheckpointer") as cp:
                cp.return_value.db_path = db
                self.assertEqual(reset_conversation(session), 2)
                session.get_session_thread_id.return_value = "missing:thread"
                self.assertEqual(reset_conversation(session), 0)

            conn = sqlite3.connect(db)
            left = sorted(r[0] for r in conn.execute(f'SELECT entry_type FROM "{table}"'))
            conn.close()
            self.assertEqual(left, ["message", "token_usage"])


if __name__ == "__main__":
    unittest.main()
