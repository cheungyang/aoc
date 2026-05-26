import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys
import re

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.job_status import job_status
from core.util import format_tool_response

class TestJobStatusTool(unittest.IsolatedAsyncioTestCase):

    @patch('tools.job_status.JobManager')
    async def test_job_status_not_found(self, mock_job_manager_class):
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        mock_manager._jobs = {}
        
        # job_status is a tool, so we call .ainvoke
        result = await job_status.ainvoke({"job_id": "unknown_job"})
        
        self.assertEqual(result, format_tool_response("job_status", payload="", errors="Job unknown_job not found."))

    @patch('tools.job_status.JobManager')
    @patch('tools.job_status.agent_call')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=unittest.mock.mock_open, read_data="mock file content")
    async def test_job_status_success_with_query_path(self, mock_open_file, mock_exists, mock_agent_call, mock_job_manager_class):
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        
        mock_job = MagicMock()
        mock_job.session_id = "session_123"
        mock_manager._jobs = {"test_job_123": mock_job}
        
        mock_exists.return_value = True
        
        # Mock agent_call.ainvoke
        mock_agent_call.ainvoke = AsyncMock(return_value="<agent_call_response><payload><status><steps>done</steps></status></payload><errors>None</errors></agent_call_response>")
        
        result = await job_status.ainvoke({"job_id": "test_job_123", "query_path": "/path/to/file"})
        
        mock_exists.assert_called_once_with("/path/to/file")
        mock_open_file.assert_called_once_with("/path/to/file", 'r')
        
        # Verify agent_call was called with the file content
        mock_agent_call.ainvoke.assert_called_once()
        args, kwargs = mock_agent_call.ainvoke.call_args
        self.assertEqual(args[0]["agent_id"], "skelly")
        self.assertIn("mock file content", args[0]["prompt"])
        
        # Verify the result is extracted correctly
        self.assertEqual(result, format_tool_response("job_status", payload="<status><steps>done</steps></status>", errors="None"))

    @patch('tools.job_status.JobManager')
    @patch('tools.job_status.FlatFileCheckpointer')
    @patch('tools.job_status.agent_call')
    async def test_job_status_success_with_checkpointer(self, mock_agent_call, mock_checkpointer_class, mock_job_manager_class):
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        
        mock_job = MagicMock()
        mock_job.session_id = "session_123"
        mock_manager._jobs = {"test_job_123": mock_job}
        
        # Mock checkpointer
        mock_checkpointer = MagicMock()
        mock_checkpointer_class.return_value = mock_checkpointer
        
        mock_checkpoint_tuple = MagicMock()
        mock_checkpoint_tuple.checkpoint = {
            "channel_values": {
                "messages": [
                    MagicMock(content="message 1"),
                    MagicMock(content="message 2")
                ]
            }
        }
        mock_checkpointer.get_tuple.return_value = mock_checkpoint_tuple
        
        # Mock agent_call.ainvoke
        mock_agent_call.ainvoke = AsyncMock(return_value="<agent_call_response><payload>llm response</payload><errors>None</errors></agent_call_response>")
        
        result = await job_status.ainvoke({"job_id": "test_job_123"})
        
        mock_checkpointer.get_tuple.assert_called_once()
        
        # Verify agent_call was called with messages content
        mock_agent_call.ainvoke.assert_called_once()
        args, kwargs = mock_agent_call.ainvoke.call_args
        self.assertIn("message 1", args[0]["prompt"])
        self.assertIn("message 2", args[0]["prompt"])
        
        self.assertEqual(result, format_tool_response("job_status", payload="llm response", errors="None"))

    @patch('tools.job_status.JobManager')
    @patch('tools.job_status.FlatFileCheckpointer')
    async def test_job_status_no_content(self, mock_checkpointer_class, mock_job_manager_class):
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        
        mock_job = MagicMock()
        mock_job.session_id = "session_123"
        mock_manager._jobs = {"test_job_123": mock_job}
        
        # Mock checkpointer returning no messages
        mock_checkpointer = MagicMock()
        mock_checkpointer_class.return_value = mock_checkpointer
        mock_checkpointer.get_tuple.return_value = None
        
        result = await job_status.ainvoke({"job_id": "test_job_123"})
        
        self.assertEqual(result, format_tool_response("job_status", payload="No status information found. Please provide a query_path.", errors="None"))

if __name__ == '__main__':
    unittest.main()
