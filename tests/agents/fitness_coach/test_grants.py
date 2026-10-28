"""fitness-coach's grants (agent.json): its own tools write its files; it can read every
source they read (the audit path), but nothing else writes."""
import os
import unittest

from core.loaders.agents_loader import AgentsLoader
from core.loaders.tools_loader import ToolsLoader
from tests.helpers import make_context

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
EXERCISE = os.path.join(REPO, "pkm", "wallet", "exercise")


class TestGrants(unittest.TestCase):
    def setUp(self):
        self.ctx = make_context(agent_id="fitness-coach")

    def allowed(self, tool, action=None, target=None):
        return ToolsLoader().check_permission(self.ctx, tool, action, target)

    def test_its_own_tools(self):
        for tool in ("workout_setup", "log_workout_session", "set_thinking"):
            self.assertTrue(self.allowed(tool), tool)

    def test_google_health_reads_sleep_and_exercise_only(self):
        self.assertTrue(self.allowed("google_health", "sleep", "sleep"))
        self.assertTrue(self.allowed("google_health", "list_exercises", "exercise"))
        self.assertFalse(self.allowed("google_health", "log_workout", "exercise"))   # logged by log_workout_session
        self.assertFalse(self.allowed("google_health", "weight", "weight"))
        self.assertFalse(self.allowed("google_health", "active_minutes", "active-minutes"))

    def test_the_exercise_files_are_read_only(self):
        for name in ("progression.jsonl", "workout_history.jsonl", "muscle_recovery.json", "library/core.json"):
            path = os.path.join(EXERCISE, name)
            self.assertTrue(self.allowed("filesystem", "read", path), name)
            for action in ("write", "overwrite", "append", "replace_block", "delete"):
                self.assertFalse(self.allowed("filesystem", action, path), (name, action))

    def test_tool_audit_reads_its_workflow(self):
        skills = AgentsLoader().get_agent("fitness-coach").get_config("skills", {})
        self.assertEqual(skills, {"coach_home_workout": {"preload": "full"}, "tool_audit": {}})
        workflow = os.path.join(REPO, "agents", "fitness-coach", "WORKFLOW.md")
        self.assertTrue(self.allowed("filesystem", "read", workflow))
        self.assertFalse(self.allowed("filesystem", "write", workflow))


if __name__ == "__main__":
    unittest.main()
