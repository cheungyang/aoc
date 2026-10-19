import json
import os
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tests.helpers import envelope, execution_context
from tests.agents.goal_setter.lib.coaching_fixture import CoachingPkm
from agents.goal_setter.tools.projects_needing_attention import projects_needing_attention

NOW = datetime(2026, 10, 16, 9, tzinfo=ZoneInfo("America/Los_Angeles"))
GUITAR = "Guitar lessons"


@patch("agents.goal_setter.tools.projects_needing_attention.get_local_now", return_value=NOW)
class TestProjectsNeedingAttentionTool(unittest.TestCase):

    def setUp(self):
        self.pkm = CoachingPkm()
        self.addCleanup(self.pkm.cleanup)
        self.pkm.use_as_config()

    def call(self, **kwargs):
        with execution_context(agent_id="goal-setter"):
            return envelope(projects_needing_attention.func(**kwargs))

    def scores(self, out):
        return [(p["name"], p["score"]) for p in json.loads(out["payload"])["projects"]]

    def test_ranked_with_reasons(self, _now):
        out = self.call(limit=1)
        self.assertEqual((out["status"], out["errors"]), ("ok", "None"))
        result = json.loads(out["payload"])
        self.assertEqual([p["name"] for p in result["projects"]], [GUITAR])
        self.assertEqual(result["more"], ["Finish a 10k by EoY 2026", "Spanish lessons"])
        self.assertEqual(result["coached_in_last_7d"], ["Project Alpha launch"])

    def test_rules_md_changes_the_weights(self, _now):
        self.pkm.write_rules("- stalled: 3  # weigh stalled projects more\n- rest_days: 3\n")
        out = self.call(explain=True)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(self.scores(out), [(GUITAR, 8), ("Finish a 10k by EoY 2026", 7), ("Spanish lessons", 7)])
        self.assertNotIn("coached_in_last_3d", json.loads(out["payload"]))  # Alpha no longer rests, and needs nothing
        self.assertIn("- pkm/agents/goal-setter/RULES.md (modified ", out["explain"])
        self.assertIn(f"- {GUITAR}: 8 = never_coached 3 + stalled 3 + ⏫ 2", out["explain"])

    def test_a_rules_md_value_that_doesnt_read_keeps_its_default(self, _now):
        self.pkm.write_rules("- stalled: lots\n")
        out = self.call()
        self.assertEqual(out["status"], "partial")
        self.assertIn("RULES.md: line 1: 'stalled: lots' can't be read", out["issues"])
        self.assertEqual(self.scores(out)[0], (GUITAR, 7))

    def test_a_missing_store_fails_and_says_which(self, _now):
        os.remove(self.pkm.tasks_db)
        out = self.call()
        self.assertEqual((out["status"], out["payload"]), ("failed", ""))
        self.assertEqual(out["issues"], "- pkm/tasks.db: not found; run its sync first (required)")


if __name__ == "__main__":
    unittest.main()
