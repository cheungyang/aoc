"""build_agent: which class an agent's config becomes."""
import unittest

from core.runtime.agent import Agent
from core.runtime.agent_factory import SCRIPT_EXECUTOR, build_agent
from core.runtime.script_executor import ScriptExecutorAgent


class TestBuildAgent(unittest.TestCase):

    def test_the_script_executor_runs_scripts(self):
        agent = build_agent(SCRIPT_EXECUTOR, {"id": SCRIPT_EXECUTOR})
        self.assertIsInstance(agent, ScriptExecutorAgent)
        self.assertEqual(agent.agent_id, SCRIPT_EXECUTOR)

    def test_every_other_agent_is_an_agent(self):
        config = {"id": "day-planner"}
        agent = build_agent("day-planner", config)
        self.assertIsInstance(agent, Agent)
        self.assertIs(agent.config, config)


if __name__ == "__main__":
    unittest.main()
