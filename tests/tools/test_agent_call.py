import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.agent_call import agent_call

class TestAgentCallTool(unittest.IsolatedAsyncioTestCase):

    @patch('tools.agent_call.AgentsLoader')
    @patch('tools.agent_call.get_job_id')
    async def test_launch_subagent(self, mock_get_job_id, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.execute = AsyncMock(return_value="agent response")
        mock_get_job_id.return_value = "job_123"
        
        result = await agent_call.ainvoke({"action": "launch_subagent", "agent_id": "agent1", "prompt": "hello"})
        
        mock_loader.get_agent.assert_called_once_with("agent1")
        mock_agent.execute.assert_called_once_with("hello", "job_123")
        self.assertEqual(result, "agent response")

    async def test_invalid_action(self):
        result = await agent_call.ainvoke({"action": "invalid_action"})
        self.assertEqual(result, "Error: Unknown action 'invalid_action'.")

    async def test_missing_args_launch(self):
        result = await agent_call.ainvoke({"action": "launch_subagent"})
        self.assertTrue(result.startswith("Error:"))

if __name__ == '__main__':
    unittest.main()
