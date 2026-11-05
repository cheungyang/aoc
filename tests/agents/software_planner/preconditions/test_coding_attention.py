"""coding_attention: the scan that wakes Elephant when the coding queues need a decision.

Each case writes made-up manifests to a throwaway software folder (the repo is
public, so nothing here comes from a real queue) and points the scan at it.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from core.scheduler.preconditions import PreconditionError, build_precondition
from core.scheduler.spec import Findings, Problems, ScheduleContext
from agents.software_planner.preconditions.coding_attention import CodingAttention

HOUR = 3600
NOW = 100 * HOUR


def halted(task_id, at, kind="verification", **fields):
    task = {"task_id": task_id, "status": "halted", "stage": "provisioned", "attempts": {"implement": 3},
            "dependencies": [],
            "last_error": {"kind": kind, "stage": "verify", "message": "exit 1: 2 tests failed", "at": at}}
    task.update(fields)
    return task


class TestDeclaration(unittest.TestCase):

    def test_software_planner_declares_it_by_type(self):
        self.assertIsInstance(build_precondition({"type": "coding_attention"}, agent_id="software-planner"),
                              CodingAttention)

    def test_other_agents_cannot(self):
        with self.assertRaises(PreconditionError):
            build_precondition({"type": "coding_attention"}, agent_id="goal-setter")


class TestScan(unittest.TestCase):

    def setUp(self):
        from core.config.settings import Config

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.software = os.path.join(self.tmp.name, "software")
        os.makedirs(self.software)
        # RULES.md is read from this test's own PKM, never the user's.
        config = Config()
        config.pkm_dir = os.path.join(self.tmp.name, "pkm")
        self.addCleanup(setattr, config, "pkm_dir", None)
        ticking = patch("graphs.coding.utils.host.tick_enabled", return_value=(True, ""))
        ticking.start()
        self.addCleanup(ticking.stop)
        self.scan = build_precondition({"type": "coding_attention"}, agent_id="software-planner")
        self.scan.software_root = self.software

    def write(self, project, queue=None, raw=None, **fields):
        folder = os.path.join(self.software, project)
        os.makedirs(folder, exist_ok=True)
        manifest = {"version": "3.0", "project_name": project, "queue": queue or [], **fields}
        with open(os.path.join(folder, "build_request.json"), "w", encoding="utf-8") as f:
            f.write(raw if raw is not None else json.dumps(manifest))

    def evaluate(self, last_success_at=None):
        return self.scan.evaluate(ScheduleContext(
            schedule_id="software-planner:coding-queue-triage", agent_id="software-planner",
            now=NOW, last_success_at=last_success_at,
        ))

    def test_a_new_halt_wakes_elephant_with_its_findings(self):
        self.write("recipe-box", [halted("feat_01_list", at=NOW - 2 * HOUR)])
        decision = self.evaluate()
        self.assertTrue(decision.has_work)
        self.assertEqual(decision.reason, "1 new: recipe-box/feat_01_list halt")
        self.assertIsInstance(decision.detail, Findings)
        self.assertEqual((decision.detail.source, decision.detail.issues), ("coding_attention", ()))
        self.assertIn("1. recipe-box/feat_01_list · halt", decision.detail.text)

    def test_what_the_last_successful_check_saw_doesnt_wake_him_again(self):
        self.write("recipe-box", [halted("feat_01_list", at=NOW - 2 * HOUR)])
        decision = self.evaluate(last_success_at=NOW - HOUR)
        self.assertFalse(decision.has_work)
        self.assertEqual(decision.reason, "nothing new in the coding queues (1 still open)")

    def test_new_items_come_with_the_still_open_ones(self):
        self.write("recipe-box", [halted("feat_01_list", at=NOW - 5 * HOUR)])
        self.write("trail-log", [halted("feat_04_map", at=NOW - 600, kind="environment")])
        decision = self.evaluate(last_success_at=NOW - HOUR)
        self.assertEqual(decision.reason, "1 new: trail-log/feat_04_map halt")
        self.assertIn("1. trail-log/feat_04_map · halt", decision.detail.text)
        self.assertIn("Still open from before (1):\n- recipe-box/feat_01_list · halt · halted 5 h ago",
                      decision.detail.text)

    def test_a_project_level_tick_error_wakes_him(self):
        self.write("recipe-box", [], tick_error={"stage": "preflight", "message": "the push identity can't push",
                                                 "at": NOW - 600})
        decision = self.evaluate(last_success_at=NOW - HOUR)
        self.assertEqual(decision.reason, "1 new: recipe-box tick_error")
        self.assertIn("ticks stop at preflight before claiming any task", decision.detail.text)

    def test_queues_with_nothing_to_decide_skip(self):
        self.write("recipe-box", [{"task_id": "feat_01_list", "status": "done", "stage": "done"}])
        decision = self.evaluate()
        self.assertFalse(decision.has_work)
        self.assertEqual(decision.reason, "nothing in the coding queues needs a decision")

    def test_a_host_that_doesnt_tick_skips(self):
        self.write("recipe-box", [halted("feat_01_list", at=NOW - 2 * HOUR)])
        with patch("graphs.coding.utils.host.tick_enabled",
                   return_value=(False, "Ticking is paused on this host (handed off).")):
            decision = self.evaluate()
        self.assertFalse(decision.has_work)
        self.assertEqual(decision.reason,
                         "this host doesn't tick the coding queues: Ticking is paused on this host (handed off).")

    def test_an_unreadable_manifest_on_a_quiet_run_is_posted(self):
        self.write("recipe-box", raw="{ not json")
        decision = self.evaluate()
        self.assertFalse(decision.has_work)
        self.assertIsInstance(decision.detail, Problems)
        [issue] = decision.detail.issues
        self.assertIn("build_request.json: can't be read, so its queue wasn't checked", issue)

    def test_an_unreadable_manifest_rides_along_on_a_run_with_work(self):
        self.write("recipe-box", raw="{ not json")
        self.write("trail-log", [halted("feat_04_map", at=NOW - 600)])
        decision = self.evaluate()
        self.assertTrue(decision.has_work)
        [issue] = decision.detail.issues
        self.assertIn("can't be read", issue)

    def test_reads_remind_after_hours_from_rules_md(self):
        review = {"task_id": "feat_02_page", "status": "awaiting_review", "stage": "awaiting_review",
                  "poll_until": NOW - 6 * HOUR}
        self.write("recipe-box", [review])
        self.assertFalse(self.evaluate().has_work)  # 6 h without a check is under the default 24

        rules = os.path.join(self.tmp.name, "pkm", "agents", "software-planner", "RULES.md")
        os.makedirs(os.path.dirname(rules))
        with open(rules, "w", encoding="utf-8") as f:
            f.write("- remind_after_hours: 4\n")

        decision = self.evaluate()
        self.assertTrue(decision.has_work)
        self.assertEqual(decision.reason, "1 new: recipe-box/feat_02_page review_dormant")


if __name__ == "__main__":
    unittest.main()
