"""Deny entries: "!<path>" refuses its actions under that path, whatever else is granted."""
import os
import unittest
from unittest.mock import patch

from core.loaders.tools_loader import ToolsLoader
from core.permissions.bundles import expand_permissions
from core.permissions.denies import deny_covers, is_deny
from tests.helpers import make_context

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DESIGNER = {
    "agents/": ["@manage"],
    "!agents/*/tools/": ["@change"],
    "!agents/*/preconditions/": ["@change"],
    "!agents/*/lib/": ["@change"],
    "!agents/*/WORKFLOW.md": ["@change"],
}


def target(path):
    return os.path.join(ROOT, path)


class TestDenyCovers(unittest.TestCase):

    def test_a_wildcard_matches_one_folder_name(self):
        key = "!agents/*/tools/"
        self.assertTrue(deny_covers(key, target("agents/fitness-coach/tools/workout_setup.py"), ROOT, "a"))
        self.assertTrue(deny_covers(key, target("agents/fitness-coach/tools"), ROOT, "a"))
        self.assertFalse(deny_covers(key, target("agents/fitness-coach/AGENTS.md"), ROOT, "a"))
        self.assertFalse(deny_covers(key, target("agents/x/y/tools/a.py"), ROOT, "a"))
        self.assertFalse(deny_covers(key, target("agents/toolsy.md"), ROOT, "a"))

    def test_a_file_key_matches_that_file_in_each_agent_folder(self):
        key = "!agents/*/WORKFLOW.md"
        self.assertTrue(deny_covers(key, target("agents/fitness-coach/WORKFLOW.md"), ROOT, "a"))
        self.assertTrue(deny_covers(key, target("agents/new-agent/WORKFLOW.md"), ROOT, "a"))
        self.assertFalse(deny_covers(key, target("agents/fitness-coach/AGENTS.md"), ROOT, "a"))
        self.assertFalse(deny_covers(key, target("agents/WORKFLOW.md"), ROOT, "a"))
        self.assertFalse(deny_covers(key, target("agents/x/docs/WORKFLOW.md"), ROOT, "a"))

    def test_plain_keys_and_agent_id(self):
        self.assertTrue(deny_covers("!tests/agents/", target("tests/agents/fitness_coach/lib/test_x.py"), ROOT, "a"))
        self.assertTrue(deny_covers("!agents/<agent_id>/lib", target("agents/me/lib/x.py"), ROOT, "me"))
        self.assertFalse(deny_covers("!agents/<agent_id>/lib", target("agents/other/lib/x.py"), ROOT, "me"))

    def test_is_deny(self):
        self.assertTrue(is_deny("!agents/"))
        self.assertFalse(is_deny("agents/"))


class TestCheckPermission(unittest.TestCase):

    def setUp(self):
        ToolsLoader._instance = None
        self.addCleanup(setattr, ToolsLoader, "_instance", None)
        self.ctx = make_context(agent_id="agent-designer")

    def allowed(self, grants, action, path):
        merged = expand_permissions({"filesystem": dict(grants)})
        with patch.object(ToolsLoader, "_merge_tool_permissions", return_value=merged):
            return ToolsLoader().check_permission(self.ctx, "filesystem", action, path)

    def test_the_agent_designer_table(self):
        rows = [
            ("write", "agents/fitness-coach/AGENTS.md", True),
            ("read", "agents/fitness-coach/tools/workout_setup.py", True),
            ("replace_block", "agents/fitness-coach/tools/workout_setup.py", False),
            ("write", "agents/new-agent/agent.json", True),
            ("write", "agents/new-agent/lib/helpers.py", False),
            ("move", "agents/x/lib/a.py", False),  # a move's destination is checked the same way
            ("delete", "agents/fitness-coach/preconditions/x.py", False),
            ("read", "agents/fitness-coach/WORKFLOW.md", True),
            ("replace_block", "agents/fitness-coach/WORKFLOW.md", False),
            ("write", "agents/new-agent/WORKFLOW.md", False),
        ]
        for action, path, expected in rows:
            with self.subTest(action=action, path=path):
                self.assertEqual(self.allowed(DESIGNER, action, target(path)), expected)

    def test_without_deny_keys_nothing_changes(self):
        self.assertTrue(self.allowed({"agents/": ["@manage"]}, "write", target("agents/a/tools/x.py")))

    def test_a_deny_beats_a_narrower_grant(self):
        grants = {"!agents/*/lib/": ["@change"], "agents/fitness-coach/lib/": ["@write"]}
        self.assertFalse(self.allowed(grants, "write", target("agents/fitness-coach/lib/x.py")))
        self.assertTrue(self.allowed(grants, "read", target("agents/fitness-coach/lib/x.py")))

    def test_the_real_agent_designer_config(self):
        loader = ToolsLoader()
        self.assertTrue(loader.check_permission(self.ctx, "filesystem", "write", target("agents/fitness-coach/AGENTS.md")))
        self.assertTrue(loader.check_permission(self.ctx, "filesystem", "read", target("agents/fitness-coach/tools/workout_setup.py")))
        self.assertFalse(loader.check_permission(self.ctx, "filesystem", "write", target("agents/fitness-coach/tools/workout_setup.py")))
        self.assertFalse(loader.check_permission(self.ctx, "filesystem", "write", target("tests/agents/fitness_coach/lib/test_progression.py")))
        self.assertTrue(loader.check_permission(self.ctx, "filesystem", "read", target("agents/fitness-coach/WORKFLOW.md")))
        self.assertFalse(loader.check_permission(self.ctx, "filesystem", "overwrite", target("agents/fitness-coach/WORKFLOW.md")))


if __name__ == "__main__":
    unittest.main()
