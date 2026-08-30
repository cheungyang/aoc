import unittest
import os
import sys
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from core.loaders.agents_loader import AgentsLoader

class TestGraphWorkerAgent(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        AgentsLoader._instance = None

    def test_graph_worker_config_exists_and_valid(self):
        loader = AgentsLoader()
        agent = loader.get_agent("graph-worker")
        self.assertIsNotNone(agent)
        self.assertEqual(agent.agent_id, "graph-worker")
        self.assertEqual(agent.config.get("name"), "Graph Worker")
        
        # Verify allowed channels includes wildcard '*'
        allowed_channels = agent.config.get("channels", [])
        self.assertIn("*", allowed_channels)

    def test_graph_worker_tools_configured(self):
        loader = AgentsLoader()
        agent = loader.get_agent("graph-worker")
        tools_cfg = agent.config.get("tools", {})
        self.assertIsInstance(tools_cfg, dict)

    async def test_agent_call_permissions_for_graph_worker(self):
        from tools.agent_call import agent_call
        from unittest.mock import patch, AsyncMock

        loader = AgentsLoader()
        agent = loader.get_agent("graph-worker")
        
        async def fake_stream(*args, **kwargs):
            yield {"type": "token", "content": "<payload>executed successfully</payload>"}
            yield {"type": "final_response", "text": "<payload>executed successfully</payload>"}

        with patch.object(agent, "execute_stream", side_effect=fake_stream) as mock_exec:
            res = await agent_call.ainvoke({
                "agent_id": "graph-worker",
                "prompt": "<playbook>Role</playbook><current_state>State</current_state><assigned_task>Task</assigned_task>",
                "channel": "content-creation"
            })
            self.assertIn("executed successfully", res)
            mock_exec.assert_called_once()

if __name__ == "__main__":
    unittest.main()
