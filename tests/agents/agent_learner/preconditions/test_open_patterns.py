"""open_patterns: Kaizen's Monday review runs only when a recurring pattern has no proposal yet."""
import os
import unittest
from unittest.mock import patch

from core.scheduler.preconditions import PreconditionError, build_precondition
from core.scheduler.spec import Findings, ScheduleContext
from core.storage.learning import CHECKED, LearningStore
from agents.agent_learner.preconditions.open_patterns import OpenPatterns

NOW = 1_800_000_000.0
DAY = 86400


def schedule_ctx():
    return ScheduleContext(schedule_id="agent-learner:friction-review", agent_id="agent-learner", now=NOW)


class TestDeclaration(unittest.TestCase):

    def test_agent_learner_declares_it_by_type(self):
        self.assertIsInstance(build_precondition({"type": "open_patterns"}, agent_id="agent-learner"), OpenPatterns)

    def test_other_agents_cannot(self):
        with self.assertRaises(PreconditionError):
            build_precondition({"type": "open_patterns"}, agent_id="day-planner")


@patch.dict(os.environ, {"LEARNING_OBSERVE": "1"})
class TestScan(unittest.TestCase):

    def setUp(self):
        self.scan = OpenPatterns({})
        self.store = LearningStore()

    def cards(self, agent, detector, sessions):
        self.store.record_friction([
            {"at": NOW - DAY - i, "detector": detector, "source": "learning_events", "source_id": f"{agent}{detector}{i}",
             "agent_id": agent, "session": session, "evidence": "Error: Both 'action' and 'path' are required",
             "evidence_role": "tool", "severity": 2} for i, session in enumerate(sessions)])

    def propose(self, pattern):
        self.store.add_proposal({"pattern": pattern, "path": "skills/x/SKILL.md", "base": "abc", "status": CHECKED},
                                at=NOW - DAY)

    def test_fires_with_the_patterns_that_need_a_proposal(self):
        self.cards("topic-researcher", "tool_failure", ["a", "a", "b"])
        self.cards("main", "rephrase", ["c"])  # one card: not recurring
        decision = self.scan.evaluate(schedule_ctx())
        self.assertTrue(decision.has_work)
        self.assertEqual(decision.reason, "1 recurring pattern(s) without a proposal")
        self.assertIsInstance(decision.detail, Findings)
        lines = decision.detail.text.splitlines()
        self.assertEqual(lines[0], "Recurring patterns without a proposal, worst first (room for 3 this week):")
        self.assertTrue(lines[1].startswith("- topic-researcher:tool_failure: 3 cards in 2 sessions, severity 6. #"))
        self.assertEqual(len(lines), 2)

    def test_skips_when_every_recurring_pattern_has_a_proposal(self):
        self.cards("topic-researcher", "tool_failure", ["a", "a", "b"])
        self.propose("topic-researcher:tool_failure")
        decision = self.scan.evaluate(schedule_ctx())
        self.assertFalse(decision.has_work)
        self.assertEqual(decision.reason, "no recurring pattern without a checked proposal")

    def test_skips_when_the_weeks_patterns_are_spent(self):
        self.cards("topic-researcher", "tool_failure", ["a", "a", "b"])
        for pattern in ("main:rephrase", "main:correction", "day-planner:round_trips"):
            self.propose(pattern)
        self.assertEqual(self.scan.evaluate(schedule_ctx()).reason,
                         "3 patterns already have a checked proposal this week")

    def test_skips_with_the_loop_off(self):
        with patch.dict(os.environ, {"LEARNING_OBSERVE": "0"}):
            self.assertEqual(self.scan.evaluate(schedule_ctx()).reason, "LEARNING_OBSERVE is off")


if __name__ == "__main__":
    unittest.main()
