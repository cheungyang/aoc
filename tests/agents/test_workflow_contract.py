"""Each agent with tools keeps the tool contract (core/tooling/workflow.py, `contract_problems`).

- an agent with modules in agents/<agent_id>/tools/ has a WORKFLOW.md;
- each module in agents/<agent_id>/tools/ exposes an @agent_tool of the same
  name, with `Reads:`, `Computes:` and `Leaves out:` lines in its docstring;
- WORKFLOW.md has a `## <tool>` section for each, and examples;
- lib/rules.py, if any, declares RULES once, each key named in WORKFLOW.md.
"""
import importlib
import unittest

from core.loaders.agent_code import agents_with_code, modules
from core.tooling.workflow import agent_workflows, contract_problems, read_workflow


class TestWorkflowContract(unittest.TestCase):

    def test_every_agent_with_tools_has_a_workflow(self):
        with_workflow = {agent_id for agent_id, _ in agent_workflows()}
        for agent_id in agents_with_code():
            if modules(agent_id, "tools"):
                with self.subTest(agent=agent_id):
                    self.assertIn(agent_id, with_workflow, f"agents/{agent_id}/ has tools but no WORKFLOW.md")

    def test_every_agent_with_a_workflow_keeps_the_contract(self):
        for agent_id, path in agent_workflows():
            with self.subTest(agent=agent_id):
                tools = {
                    name: getattr(importlib.import_module(dotted), name, None)
                    for name, dotted in modules(agent_id, "tools").items()
                }
                rules_module = modules(agent_id, "lib").get("rules")
                rules = getattr(importlib.import_module(rules_module), "RULES", ()) if rules_module else None
                self.assertEqual(contract_problems(read_workflow(path), tools, rules), [])


if __name__ == "__main__":
    unittest.main()
