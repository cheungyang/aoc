import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.agent_call import agent_call

class TestAgentCallTool(unittest.IsolatedAsyncioTestCase):

    @patch('tools.agent_call.SubagentManager')
    async def test_launch_subagent(self, mock_subagent_manager):
        mock_manager = MagicMock()
        mock_subagent_manager.return_value = mock_manager
        mock_manager.launch_task.return_value = "job_123"
        
        result = await agent_call.ainvoke({"action": "launch_subagent", "agent_id": "agent1", "prompt": "hello"})
        
        mock_manager.launch_task.assert_called_once_with("agent1", "hello")
        self.assertEqual(result, "Task launched with job_id: job_123")

    @patch('tools.agent_call.SubagentManager')
    async def test_check_subagent(self, mock_subagent_manager):
        mock_manager = MagicMock()
        mock_subagent_manager.return_value = mock_manager
        mock_manager.check_task.return_value = {
            "status": "completed",
            "result": "success",
            "question": None
        }
        
        result = await agent_call.ainvoke({"action": "check_subagent", "job_id": "job_123"})
        
        mock_manager.check_task.assert_called_once_with("job_123")
        self.assertIn("Status: completed", result)
        self.assertIn("Result: success", result)

    @patch('tools.agent_call.SubagentManager')
    async def test_update_subagent(self, mock_subagent_manager):
        mock_manager = MagicMock()
        mock_subagent_manager.return_value = mock_manager
        mock_manager.update_task.return_value = "Updated"
        
        result = await agent_call.ainvoke({"action": "update_subagent", "job_id": "job_123", "user_input": "hello"})
        
        mock_manager.update_task.assert_called_once_with("job_123", "hello")
        self.assertEqual(result, "Update status: Updated")

    @patch('tools.agent_call.SubagentManager')
    async def test_cancel_subagent(self, mock_subagent_manager):
        mock_manager = MagicMock()
        mock_subagent_manager.return_value = mock_manager
        mock_manager.cancel_task.return_value = "Cancelled"
        
        result = await agent_call.ainvoke({"action": "cancel_subagent", "job_id": "job_123"})
        
        mock_manager.cancel_task.assert_called_once_with("job_123")
        self.assertEqual(result, "Cancel status: Cancelled")

    async def test_invalid_action(self):
        result = await agent_call.ainvoke({"action": "invalid_action"})
        self.assertEqual(result, "Error: Unknown action 'invalid_action'.")

    async def test_missing_args_launch(self):
        result = await agent_call.ainvoke({"action": "launch_subagent"})
        self.assertTrue(result.startswith("Error:"))

if __name__ == '__main__':
    unittest.main()
