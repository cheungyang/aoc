import unittest
<<<<<<< HEAD
from unittest.mock import patch, MagicMock
import os
import sys
=======
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys
import re
>>>>>>> 6b8ec8a8fdc97b58e4988c56fc1189e01e2567c3

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.job_status import job_status
from core.util import format_tool_response

<<<<<<< HEAD
class TestJobStatusTool(unittest.TestCase):

    @patch('tools.job_status.JobManager')
    def test_job_status_not_found(self, mock_job_manager_class):
=======
class TestJobStatusTool(unittest.IsolatedAsyncioTestCase):

    @patch('tools.job_status.JobManager')
    async def test_job_status_not_found(self, mock_job_manager_class):
>>>>>>> 6b8ec8a8fdc97b58e4988c56fc1189e01e2567c3
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        mock_manager._jobs = {}
        
<<<<<<< HEAD
        result = job_status.func(job_id="unknown_job")
=======
        # job_status is a tool, so we call .ainvoke
        result = await job_status.ainvoke({"job_id": "unknown_job"})
>>>>>>> 6b8ec8a8fdc97b58e4988c56fc1189e01e2567c3
        
        self.assertEqual(result, format_tool_response("job_status", payload="", errors="Job unknown_job not found."))

    @patch('tools.job_status.JobManager')
<<<<<<< HEAD
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=unittest.mock.mock_open, read_data="Agent status from file")
    def test_job_status_with_query_path(self, mock_open, mock_exists, mock_job_manager_class):
=======
    @patch('tools.job_status.agent_call')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=unittest.mock.mock_open, read_data="mock file content")
    async def test_job_status_success_with_query_path(self, mock_open_file, mock_exists, mock_agent_call, mock_job_manager_class):
>>>>>>> 6b8ec8a8fdc97b58e4988c56fc1189e01e2567c3
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        
        mock_job = MagicMock()
        mock_job.session_id = "session_123"
        mock_manager._jobs = {"test_job_123": mock_job}
        
        mock_exists.return_value = True
        
<<<<<<< HEAD
        result = job_status.func(job_id="test_job_123", query_path="/path/to/status.log")
        
        mock_open.assert_called_once_with("/path/to/status.log", 'r')
        self.assertEqual(result, format_tool_response("job_status", payload="Agent status from file", errors="None"))

    @patch('tools.job_status.JobManager')
    @patch('tools.job_status.FlatFileCheckpointer')
    def test_job_status_from_checkpoint(self, mock_checkpointer_class, mock_job_manager_class):
=======
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
>>>>>>> 6b8ec8a8fdc97b58e4988c56fc1189e01e2567c3
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        
        mock_job = MagicMock()
        mock_job.session_id = "session_123"
        mock_manager._jobs = {"test_job_123": mock_job}
        
<<<<<<< HEAD
=======
        # Mock checkpointer
>>>>>>> 6b8ec8a8fdc97b58e4988c56fc1189e01e2567c3
        mock_checkpointer = MagicMock()
        mock_checkpointer_class.return_value = mock_checkpointer
        
        mock_checkpoint_tuple = MagicMock()
<<<<<<< HEAD
        
        # Mock LangChain messages
        msg1 = MagicMock()
        msg1.type = "human"
        msg1.content = "hello"
        
        msg2 = MagicMock()
        msg2.type = "ai"
        msg2.content = "I am working on steps 1 and 2. 50% done."
        
        mock_checkpoint_tuple.checkpoint = {
            "channel_values": {
                "messages": [msg1, msg2]
=======
        mock_checkpoint_tuple.checkpoint = {
            "channel_values": {
                "messages": [
                    MagicMock(content="message 1"),
                    MagicMock(content="message 2")
                ]
>>>>>>> 6b8ec8a8fdc97b58e4988c56fc1189e01e2567c3
            }
        }
        mock_checkpointer.get_tuple.return_value = mock_checkpoint_tuple
        
<<<<<<< HEAD
        result = job_status.func(job_id="test_job_123")
        
        expected_payload = "Job test_job_123 status (Session: session_123):\n"
        expected_payload += "Latest response from agent:\nI am working on steps 1 and 2. 50% done.\n\n"
        expected_payload += "Note: To get specific progress (steps, %, artifacts), please provide a query_path if the agent writes status to a file."
        
        self.assertEqual(result, format_tool_response("job_status", payload=expected_payload, errors="None"))

    @patch('tools.job_status.JobManager')
    @patch('tools.job_status.FlatFileCheckpointer')
    def test_job_status_no_checkpoint(self, mock_checkpointer_class, mock_job_manager_class):
=======
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
>>>>>>> 6b8ec8a8fdc97b58e4988c56fc1189e01e2567c3
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        
        mock_job = MagicMock()
        mock_job.session_id = "session_123"
        mock_manager._jobs = {"test_job_123": mock_job}
        
<<<<<<< HEAD
=======
        # Mock checkpointer returning no messages
>>>>>>> 6b8ec8a8fdc97b58e4988c56fc1189e01e2567c3
        mock_checkpointer = MagicMock()
        mock_checkpointer_class.return_value = mock_checkpointer
        mock_checkpointer.get_tuple.return_value = None
        
<<<<<<< HEAD
        result = job_status.func(job_id="test_job_123")
        
        expected_payload = "No checkpoint found for session session_123. Please provide a query_path to the agent's status file."
        self.assertEqual(result, format_tool_response("job_status", payload=expected_payload, errors="None"))
=======
        result = await job_status.ainvoke({"job_id": "test_job_123"})
        
        self.assertEqual(result, format_tool_response("job_status", payload="No status information found. Please provide a query_path.", errors="None"))
>>>>>>> 6b8ec8a8fdc97b58e4988c56fc1189e01e2567c3

if __name__ == '__main__':
    unittest.main()
