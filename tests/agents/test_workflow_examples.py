"""Every agents/<agent_id>/WORKFLOW.md example holds against the code, so the text can't drift.

The format and the runner: core/tooling/workflow.py.
"""
import unittest

from core.tooling.workflow import agent_workflows, read_workflow, run_examples


class TestWorkflowExamples(unittest.TestCase):

    def test_every_example_holds(self):
        for agent_id, path in agent_workflows():
            with self.subTest(agent=agent_id):
                run = run_examples(read_workflow(path))
                self.assertEqual(run.failed, 0, f"agents/{agent_id}/WORKFLOW.md:\n{run.output}")


if __name__ == "__main__":
    unittest.main()
