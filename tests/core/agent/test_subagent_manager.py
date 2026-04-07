import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys
import asyncio

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.subagent_manager import SubagentManager

class TestSubagentManager(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Reset singleton
        SubagentManager._instance = None
        self.manager = SubagentManager()

    @patch('core.agent.subagent_manager.AgentsLoader')
    async def test_launch_task(self, mock_agents_loader_class):
        
        # Mock AgentsLoader and Agent
        mock_loader = MagicMock()
        mock_agents_loader_class.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.execute = AsyncMock(return_value="subagent response")
        mock_loader.get_agent = AsyncMock(return_value=mock_agent)

        job_id = self.manager.launch_task("agent-designer", "prompt", "session_id")
        
        self.assertIsNotNone(job_id)
        
        # Wait for the task to complete (since it runs in background)
        task_info = self.manager.tasks[job_id]
        await task_info["task_obj"]
        
        self.assertEqual(task_info["status"], "success")
        self.assertEqual(task_info["result"], "subagent response")
        mock_agent.execute.assert_called_once_with("prompt", job_id)

    def test_check_task_not_found(self):
        result = self.manager.check_task("non_existent_id")
        self.assertEqual(result["status"], "not_found")

    def test_cancel_task_not_found(self):
        result = self.manager.cancel_task("non_existent_id")
        self.assertEqual(result, "not_found")

if __name__ == '__main__':
    unittest.main()
