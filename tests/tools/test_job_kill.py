import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import time

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.job_kill import job_kill
from core.util import format_tool_response

class TestJobKillTool(unittest.TestCase):

    @patch('tools.job_kill.JobManager')
    @patch('tools.job_kill.SqliteCheckpointer')
    @patch('tools.job_kill.time.sleep') # Mock sleep to speed up tests
    def test_job_kill_success(self, mock_sleep, mock_checkpointer_class, mock_job_manager_class):
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        
        # Mock job
        mock_job = MagicMock()
        mock_job.session_id = "session_123"
        mock_job.status = "killed" # Simulate it being killed immediately for polling success
        mock_manager._jobs = {"test_job_123": mock_job}
        
        # Mock checkpointer
        mock_checkpointer = MagicMock()
        mock_checkpointer_class.return_value = mock_checkpointer
        
        mock_checkpoint_tuple = MagicMock()
        mock_checkpoint_tuple.checkpoint = {
            "channel_values": {
                "messages": [
                    MagicMock(role="human", content="hello"),
                    MagicMock(role="ai", content="hi there")
                ]
            }
        }
        mock_checkpointer.get_tuple.return_value = mock_checkpoint_tuple
        
        result = job_kill.func(job_id="test_job_123")
        
        mock_manager.kill_job.assert_called_once_with("test_job_123")
        
        expected_summary = "Job test_job_123 killed successfully.\n"
        expected_summary += "Intermediate results (Session: session_123):\n"
        expected_summary += "--- human ---\nhello\n"
        expected_summary += "--- ai ---\nhi there\n"
        
        self.assertEqual(result, format_tool_response("job_kill", payload=expected_summary, errors="None"))

    @patch('tools.job_kill.JobManager')
    def test_job_kill_not_found(self, mock_job_manager_class):
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        mock_manager._jobs = {}
        
        result = job_kill.func(job_id="unknown_job")
        
        self.assertEqual(result, format_tool_response("job_kill", payload="", errors="Job unknown_job not found."))

    @patch('tools.job_kill.JobManager')
    @patch('tools.job_kill.time.sleep')
    def test_job_kill_timeout(self, mock_sleep, mock_job_manager_class):
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        
        mock_job = MagicMock()
        mock_job.session_id = "session_123"
        mock_job.status = "killing" # Remains killing to trigger timeout
        mock_manager._jobs = {"test_job_123": mock_job}
        
        result = job_kill.func(job_id="test_job_123")
        
        self.assertEqual(result, format_tool_response("job_kill", payload="Job test_job_123 did not stop in time. Current status: killing", errors="None"))

if __name__ == '__main__':
    unittest.main()
