import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.job_status import job_status
from core.util import format_tool_response

class TestJobStatusTool(unittest.TestCase):

    @patch('tools.job_status.JobManager')
    def test_job_status_not_found(self, mock_job_manager_class):
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        mock_manager._jobs = {}
        
        result = job_status.func(job_id="unknown_job")
        
        self.assertEqual(result, format_tool_response("job_status", payload="", errors="Job unknown_job not found."))

    @patch('tools.job_status.JobManager')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=unittest.mock.mock_open, read_data="Agent status from file")
    def test_job_status_with_query_path(self, mock_open, mock_exists, mock_job_manager_class):
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        
        mock_job = MagicMock()
        mock_job.session_id = "session_123"
        mock_manager._jobs = {"test_job_123": mock_job}
        
        mock_exists.return_value = True
        
        result = job_status.func(job_id="test_job_123", query_path="/path/to/status.log")
        
        mock_open.assert_called_once_with("/path/to/status.log", 'r')
        self.assertEqual(result, format_tool_response("job_status", payload="Agent status from file", errors="None"))

    @patch('tools.job_status.JobManager')
    @patch('tools.job_status.FlatFileCheckpointer')
    def test_job_status_from_checkpoint(self, mock_checkpointer_class, mock_job_manager_class):
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        
        mock_job = MagicMock()
        mock_job.session_id = "session_123"
        mock_manager._jobs = {"test_job_123": mock_job}
        

        mock_checkpointer = MagicMock()
        mock_checkpointer_class.return_value = mock_checkpointer
        
        mock_checkpoint_tuple = MagicMock()
        
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
            }
        }
        mock_checkpointer.get_tuple.return_value = mock_checkpoint_tuple
        
        result = job_status.func(job_id="test_job_123")
        
        expected_payload = "Job test_job_123 status (Session: session_123):\n"
        expected_payload += "Latest response from agent:\nI am working on steps 1 and 2. 50% done.\n\n"
        expected_payload += "Note: To get specific progress (steps, %, artifacts), please provide a query_path if the agent writes status to a file."
        
        self.assertEqual(result, format_tool_response("job_status", payload=expected_payload, errors="None"))

    @patch('tools.job_status.JobManager')
    @patch('tools.job_status.FlatFileCheckpointer')
    def test_job_status_no_checkpoint(self, mock_checkpointer_class, mock_job_manager_class):
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        
        mock_job = MagicMock()
        mock_job.session_id = "session_123"
        mock_manager._jobs = {"test_job_123": mock_job}
        

        mock_checkpointer = MagicMock()
        mock_checkpointer_class.return_value = mock_checkpointer
        mock_checkpointer.get_tuple.return_value = None
        
        result = job_status.func(job_id="test_job_123")
        
        expected_payload = "No checkpoint found for session session_123. Please provide a query_path to the agent's status file."
        self.assertEqual(result, format_tool_response("job_status", payload=expected_payload, errors="None"))

if __name__ == '__main__':
    unittest.main()
