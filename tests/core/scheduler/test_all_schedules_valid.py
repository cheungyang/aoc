"""Every real `agents/*/agent.json` schedule must pass the scheduler's validator.

A rejected schedule never runs, so a typo in an agent.json would otherwise be
found in a channel alert at 3am. This catches it in CI.
"""
import glob
import json
import os
import unittest

from core.scheduler.registry import load_agent_schedules
from core.scheduler.spec import PromptSchedule, ScriptSchedule, SessionPolicy
from tests.core.scheduler import use_real_croniter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
AGENTS = sorted(glob.glob(os.path.join(ROOT, "agents", "*", "agent.json")))

# Agents whose scheduled runs genuinely build on the previous run's conversation.
PERSISTENT_AGENTS = {"goal-setter", "topic-researcher"}


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class TestAllSchedulesValid(unittest.TestCase):
    def setUp(self):
        cron = use_real_croniter()
        cron.__enter__()
        self.addCleanup(cron.__exit__, None, None, None)

    def test_agent_files_found(self):
        self.assertTrue(AGENTS)

    def test_every_schedule_is_accepted(self):
        for path in AGENTS:
            agent_id = os.path.basename(os.path.dirname(path))
            with self.subTest(agent=agent_id):
                result = load_agent_schedules(agent_id, load(path))
                self.assertEqual(
                    [r.message() for r in result.rejections], [],
                    f"{agent_id} has schedules the scheduler would reject",
                )

    def test_session_policies(self):
        for path in AGENTS:
            agent_id = os.path.basename(os.path.dirname(path))
            for spec in load_agent_schedules(agent_id, load(path)).specs:
                if not isinstance(spec, PromptSchedule):
                    continue
                expected = (
                    SessionPolicy.PERSISTENT if agent_id in PERSISTENT_AGENTS
                    else SessionPolicy.STATELESS
                )
                with self.subTest(schedule=spec.schedule_id):
                    self.assertIs(spec.session_policy, expected)

    def test_prompts_do_not_carry_emptiness_checks(self):
        """That is the precondition's job; the model must not be paid to find nothing."""
        for path in AGENTS:
            agent_id = os.path.basename(os.path.dirname(path))
            for spec in load_agent_schedules(agent_id, load(path)).specs:
                if isinstance(spec, PromptSchedule):
                    with self.subTest(schedule=spec.schedule_id):
                        self.assertNotIn("If empty, terminate silently", spec.prompt)

    def test_schedule_ids_are_unique(self):
        seen = {}
        for path in AGENTS:
            agent_id = os.path.basename(os.path.dirname(path))
            for spec in load_agent_schedules(agent_id, load(path)).specs:
                self.assertNotIn(spec.schedule_id, seen)
                seen[spec.schedule_id] = path

    def test_coding_tick_stays_every_five_minutes_on_software_dev(self):
        specs = load_agent_schedules(
            "script-executor", load(os.path.join(ROOT, "agents", "script-executor", "agent.json"))
        ).specs
        tick = [s for s in specs if isinstance(s, ScriptSchedule) and s.script == "coding_tick.py"]
        self.assertEqual(len(tick), 1)
        self.assertEqual(tick[0].cron, "*/5 * * * *")
        self.assertEqual(tick[0].channel, "software-dev")
        self.assertIsNone(tick[0].thread)

    def test_job_reaper_is_a_script_executor_schedule(self):
        specs = load_agent_schedules(
            "script-executor", load(os.path.join(ROOT, "agents", "script-executor", "agent.json"))
        ).specs
        reaper = [s for s in specs if isinstance(s, ScriptSchedule) and s.script == "reap_jobs.py"]
        self.assertEqual(len(reaper), 1)
        self.assertEqual(reaper[0].cron, "*/5 * * * *")


class TestSchedulerTestPairing(unittest.TestCase):
    """One test module per `core/scheduler` module."""

    def test_every_module_has_a_test(self):
        modules = sorted(
            os.path.splitext(os.path.basename(p))[0]
            for p in glob.glob(os.path.join(ROOT, "core", "scheduler", "*.py"))
            if not p.endswith("__init__.py")
        )
        self.assertTrue(modules)
        here = os.path.dirname(__file__)
        missing = [m for m in modules if not os.path.exists(os.path.join(here, f"test_{m}.py"))]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
