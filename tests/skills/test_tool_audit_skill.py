"""The tool_audit skill: its grants reach the agent's own WORKFLOW.md and RULES.md, and nothing else."""
import copy
import json
import os
import re
import unittest
from unittest.mock import patch

from core.loaders.tools_loader import ToolsLoader
from core.permissions.bundles import expand_permissions
from tests.helpers import make_context

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SKILL_DIR = os.path.join(REPO, "skills", "tool_audit")


def skill_json():
    with open(os.path.join(SKILL_DIR, "skill.json"), encoding="utf-8") as f:
        return json.load(f)


class TestToolAuditSkill(unittest.TestCase):

    def test_the_skill_reads_only_its_own_agents_workflow_and_rules(self):
        merged = copy.deepcopy(skill_json()["tools"])
        expand_permissions(merged)
        ctx = make_context(agent_id="meal-planner")
        own_workflow = os.path.join(REPO, "agents", "meal-planner", "WORKFLOW.md")
        own_rules = os.path.join(REPO, "pkm", "agents", "meal-planner", "RULES.md")
        with patch.object(ToolsLoader, "_merge_tool_permissions", return_value=merged):
            loader = ToolsLoader()
            self.assertTrue(loader.check_permission(ctx, "filesystem", "read", own_workflow))
            self.assertTrue(loader.check_permission(ctx, "filesystem", "read", own_rules))
            self.assertFalse(loader.check_permission(ctx, "filesystem", "replace_block", own_rules))
            self.assertFalse(loader.check_permission(ctx, "filesystem", "write", own_workflow))
            self.assertFalse(loader.check_permission(
                ctx, "filesystem", "read", os.path.join(REPO, "agents", "fitness-coach", "WORKFLOW.md")))
            self.assertTrue(loader.check_permission(ctx, "set_thinking"))

    def test_the_text_matches_the_grants(self):
        with open(os.path.join(SKILL_DIR, "SKILL.md"), encoding="utf-8") as f:
            text = f.read()
        config = skill_json()
        self.assertEqual(re.search(r"^name: (\S+)$", text, re.MULTILINE).group(1), config["skill_id"])
        self.assertIn(re.search(r"^description: (.+)$", text, re.MULTILINE).group(1), config["description"])
        for needle in ("explain=true", 'set_thinking("high"', "WORKFLOW.md", "RULES.md"):
            self.assertIn(needle, text)
        self.assertIn("set_thinking", config["tools"])


if __name__ == "__main__":
    unittest.main()
