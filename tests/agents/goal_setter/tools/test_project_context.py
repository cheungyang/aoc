import json
import os
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tests.helpers import envelope, execution_context
from tests.agents.goal_setter.lib.coaching_fixture import CoachingPkm
from agents.goal_setter.tools.project_context import project_context

NOW = datetime(2026, 10, 16, 9, tzinfo=ZoneInfo("America/Los_Angeles"))


@patch("agents.goal_setter.tools.project_context.get_local_now", return_value=NOW)
class TestProjectContextTool(unittest.TestCase):

    def setUp(self):
        self.pkm = CoachingPkm()
        self.addCleanup(self.pkm.cleanup)
        self.pkm.use_as_config()

    def call(self, **kwargs):
        with execution_context(agent_id="goal-setter"):
            return envelope(project_context.func(**kwargs))

    def test_returns_the_context(self, _now):
        out = self.call(name="#p/10k")
        self.assertEqual((out["status"], out["issues"], out["errors"]), ("ok", "None", "None"))
        result = json.loads(out["payload"])
        self.assertEqual(result["project"]["name"], "Finish a 10k by EoY 2026")
        self.assertEqual(result["coaching"]["days_since_coached"], 23)
        self.assertNotIn("explain", out)

    def test_explain_lists_what_was_read_and_the_inputs(self, _now):
        explain = self.call(name="10 k", explain=True)["explain"]
        self.assertIn("- pkm/projects.db (modified ", explain)
        self.assertIn("- pkm/vault/projects/Finish a 10k by June 2026.md (modified ", explain)
        self.assertIn("- last coached 2026-09-23: the later of the dossier's last_coached (2026-09-23)", explain)

    def test_an_unknown_name_lists_the_executing_projects(self, _now):
        out = self.call(name="running goal")
        self.assertEqual((out["status"], out["payload"]), ("failed", ""))
        self.assertTrue(out["errors"].startswith("Error: No project matches 'running goal'. Executing projects: "
                                                 "Finish a 10k by EoY 2026"))

    def test_a_dossier_line_that_doesnt_read_makes_it_partial(self, _now):
        self.pkm.write("wiki/project-coaching/Finish a 10k by June 2026.md",
                       "---\nproject_name: Finish a 10k by June 2026\nlast_coached: last week\n---\nNotes.\n")
        out = self.call(name="10k")
        self.assertEqual(out["status"], "partial")
        self.assertIn("pkm/wiki/project-coaching/Finish a 10k by June 2026.md: 1 line not recognized\n"
                      "    last_coached: last week", out["issues"])
        self.assertEqual(json.loads(out["payload"])["coaching"]["last_coached"], "2026-09-23")  # from the note

    def test_a_missing_store_fails_and_says_which(self, _now):
        os.remove(self.pkm.tasks_db)
        out = self.call(name="10k")
        self.assertEqual((out["status"], out["payload"]), ("failed", ""))
        self.assertEqual(out["issues"], "- pkm/tasks.db: not found; run its sync first (required)")

    def test_needs_an_execution_context(self, _now):
        out = envelope(project_context.func(name="10k"))
        self.assertEqual(out["status"], "failed")
        self.assertIn("no active execution context", out["errors"])


if __name__ == "__main__":
    unittest.main()
