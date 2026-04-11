import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.agent_call import agent_call

class TestAgentCallTool(unittest.IsolatedAsyncioTestCase):

    @patch('tools.agent_call.AgentsLoader')
    @patch('core.agent.job_manager.JobManager.new_job_id')
    async def test_agent_call_success(self, mock_get_job_id, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.execute = AsyncMock(return_value="agent response")
        mock_get_job_id.return_value = "job_123"
        
        result = await agent_call.ainvoke({"agent_id": "agent1", "prompt": "hello"})
        
        mock_loader.get_agent.assert_called_once_with("agent1")
        mock_agent.execute.assert_called_once_with("hello", source="tool", job_id="job_123")
        self.assertEqual(result, "agent response")

    @patch('tools.agent_call.AgentsLoader')
    @patch('core.agent.job_manager.JobManager.new_job_id')
    async def test_agent_call_async(self, mock_get_job_id, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.execute = AsyncMock(return_value="agent response")
        mock_get_job_id.return_value = "job_123"
        
        result = await agent_call.ainvoke({"agent_id": "agent1", "prompt": "hello", "run_async": True})
        
        mock_loader.get_agent.assert_called_once_with("agent1")
        import asyncio
        await asyncio.sleep(0.1)
        mock_agent.execute.assert_called_once_with("hello", source="tool", job_id="job_123")

        self.assertIn("Successfully triggered agent 'agent1'", result)


    async def test_missing_args(self):
        with self.assertRaises(Exception):
            await agent_call.ainvoke({"agent_id": "agent1"})



if __name__ == '__main__':
    unittest.main()
