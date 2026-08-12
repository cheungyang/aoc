import unittest
from unittest.mock import patch, MagicMock, AsyncMock
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
    @patch('tools.job_status.SqliteSessionStore')
    @patch('tools.job_status.agent_call')
    def test_job_status_from_history(self, mock_agent_call, mock_session_store_class, mock_job_manager_class):
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        
        mock_job = MagicMock()
        mock_job.session_id = "session_123"
        mock_manager._jobs = {"test_job_123": mock_job}
        
        mock_session_store = MagicMock()
        mock_session_store_class.return_value = mock_session_store
        
        mock_history = [
            {"from": "user", "message": "hello", "ts": 123},
            {"from": "ai", "message": "I am working on steps 1 and 2. 50% done.", "ts": 124}
        ]
        mock_session_store.load_history.return_value = mock_history
        
        mock_agent_call.ainvoke = AsyncMock(return_value="<payload>Compiled progress</payload>")
        
        result = job_status.func(job_id="test_job_123")
        
        expected_payload = "Job test_job_123 status (Session: session_123):\n"
        expected_payload += "AI messages: 1\n"
        expected_payload += "\n--- AI Messages ---\n"
        expected_payload += "[1] I am working on steps 1 and 2. 50% done.\n"
        expected_payload += "-------------------\n\n"
        expected_payload += "Compiled Progress from skill-runner:\nCompiled progress\n"
        
        self.assertEqual(result, format_tool_response("job_status", payload=expected_payload, errors="None"))

    @patch('tools.job_status.JobManager')
    @patch('tools.job_status.SqliteSessionStore')
    @patch('tools.job_status.agent_call')
    def test_job_status_fallback_to_last_message(self, mock_agent_call, mock_session_store_class, mock_job_manager_class):
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        
        mock_job = MagicMock()
        mock_job.session_id = "session_123"
        mock_manager._jobs = {"test_job_123": mock_job}
        
        mock_session_store = MagicMock()
        mock_session_store_class.return_value = mock_session_store
        
        mock_history = [
            {"from": "user", "message": "hello", "ts": 123},
            {"from": "system", "message": "Tool Output: success", "ts": 124}
        ]
        mock_session_store.load_history.return_value = mock_history
        
        mock_agent_call.ainvoke = AsyncMock(return_value="<payload>Compiled progress</payload>")
        
        result = job_status.func(job_id="test_job_123")
        
        expected_payload = "Job test_job_123 status (Session: session_123):\n"
        expected_payload += "AI messages: 0\n"
        expected_payload += "\n--- AI Messages ---\n"
        expected_payload += "-------------------\n\n"
        expected_payload += "Compiled Progress from skill-runner:\nCompiled progress\n"
        
        self.assertEqual(result, format_tool_response("job_status", payload=expected_payload, errors="None"))

    @patch('tools.job_status.JobManager')
    @patch('tools.job_status.SqliteSessionStore')
    def test_job_status_no_history(self, mock_session_store_class, mock_job_manager_class):
        mock_manager = MagicMock()
        mock_job_manager_class.return_value = mock_manager
        
        mock_job = MagicMock()
        mock_job.session_id = "session_123"
        mock_manager._jobs = {"test_job_123": mock_job}
        
        mock_session_store = MagicMock()
        mock_session_store_class.return_value = mock_session_store
        mock_session_store.load_history.return_value = []
        
        result = job_status.func(job_id="test_job_123")
        
        expected_payload = "No session history found for session session_123."
        self.assertEqual(result, format_tool_response("job_status", payload=expected_payload, errors="None"))

if __name__ == '__main__':
    unittest.main()
