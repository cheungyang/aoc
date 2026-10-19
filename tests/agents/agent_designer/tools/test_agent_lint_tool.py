"""Tests for agents/agent-designer/tools/agent_lint.py, an @agent_tool."""
import unittest
from unittest.mock import patch

from agents.agent_designer.tools import agent_lint as tool_module
from core.tooling import AgentTool
from tests.helpers import envelope, execution_context


class TestAgentLintTool(unittest.TestCase):

    def invoke(self, agent="agent-designer", **args):
        with execution_context(agent_id=agent):
            return envelope(tool_module.agent_lint.invoke(args))

    def test_it_is_an_agent_tool_with_the_contract_lines(self):
        tool = tool_module.agent_lint
        self.assertIsInstance(tool, AgentTool)
        for label in ("\nReads: ", "\nComputes: ", "\nLeaves out: "):
            self.assertIn(label, tool.description)

    def test_one_agent(self):
        with patch("agents.agent_designer.lib.lint.tool_schema_tokens", return_value=(0, [])):
            out = self.invoke(agent_id=" agent-designer ")
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["payload"].startswith("agent-designer: "))
        self.assertIn("budget: persona", out["payload"])
        self.assertEqual(out["errors"], "None")

    def test_explain_lists_the_agent_files_it_read(self):
        with patch("agents.agent_designer.lib.lint.tool_schema_tokens", return_value=(0, [])):
            out = self.invoke(agent_id="agent-designer", explain=True)
        self.assertIn("agents/agent-designer/agent.json (modified ", out["explain"])

    def test_every_agent_when_no_id_is_given(self):
        with patch("agents.agent_designer.lib.lint.lint_agents", return_value=[]) as lint_agents:
            out = self.invoke()
        lint_agents.assert_called_once_with(None)
        self.assertEqual(out["payload"], "0 agents, 0 findings\n\n")

    def test_unknown_agent(self):
        out = self.invoke(agent_id="no-such-agent")
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["errors"], "Error: agents/no-such-agent/agent.json does not exist")

    def test_a_failure_is_reported_not_raised(self):
        with patch("agents.agent_designer.lib.lint.lint_agents", side_effect=RuntimeError("boom")):
            out = self.invoke(agent_id="agent-designer")
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["errors"], "Error in agent_lint: RuntimeError: boom")

    def test_an_agent_without_the_grant_is_refused(self):
        with patch("agents.agent_designer.lib.lint.lint_agents") as lint_agents:
            out = self.invoke(agent="meal-planner", agent_id="meal-planner")
        lint_agents.assert_not_called()
        self.assertIn("does not have permission to use agent_lint", out["errors"])


if __name__ == "__main__":
    unittest.main()
