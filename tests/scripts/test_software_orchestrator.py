import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import json
import importlib.util
import datetime
import ast

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# Import software-orchestrator.py
spec = importlib.util.spec_from_file_location("software_orchestrator", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "software-orchestrator.py")))
so = importlib.util.module_from_spec(spec)
spec.loader.exec_module(so)

class TestSoftwareOrchestrator(unittest.TestCase):

    @patch('subprocess.run')
    def test_run_command_success(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = "output"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = so.run_command("ls")
        
        self.assertEqual(result, "output")

    @patch.object(so, 'gh')
    def test_run_gh_tool_success(self, mock_gh):
        mock_gh.invoke.return_value = "<payload>my data</payload>"
        
        result = so.run_gh_tool("issue list")
        
        self.assertEqual(result, "my data")

    @patch.object(so, 'gh')
    def test_run_gh_tool_no_payload(self, mock_gh):
        mock_gh.invoke.return_value = "no payload here"
        
        result = so.run_gh_tool("issue list")
        
        self.assertIsNone(result)

    @patch.object(so, 'run_job_list_tool')
    @patch.object(so, 'run_job_kill_tool')
    @patch.object(so, 'run_gh_tool')
    def test_zombie_hunter(self, mock_gh, mock_job_kill, mock_job_list):
        three_hours_ago = datetime.datetime.now() - datetime.timedelta(hours=3)
        started_str = three_hours_ago.strftime('%Y-%m-%d %H:%M:%S')
        
        mock_job_list.return_value = f"[{{'job_id': '1', 'agent_id': 'agent1', 'started': '{started_str}'}}]"
        mock_gh.return_value = "[{\"number\": 1}]"
        mock_job_kill.return_value = "success"
        
        so.zombie_hunter()
        
        # Verify job_kill was called
        self.assertTrue(mock_job_kill.called)
        # Verify gh calls were made
        self.assertTrue(mock_gh.called)

    @patch.object(so, 'run_gh_tool')
    def test_tdd_blocked_rescuer(self, mock_gh):
        mock_gh.side_effect = [
            "[{\"number\": 1, \"assignees\": [{\"login\": \"user1\"}]}]", # list
            "None", # edit remove assignee
            "None", # edit labels
            "None"  # comment
        ]
        
        so.tdd_blocked_rescuer()
        
        self.assertEqual(mock_gh.call_count, 4)

    @patch.object(so, 'run_job_list_tool')
    @patch.object(so, 'run_gh_tool')
    @patch.object(so, 'run_agent_call_tool')
    def test_spawn_coder_if_needed(self, mock_agent_call, mock_gh, mock_job_list):
        mock_job_list.return_value = "[]" # 0 active jobs
        mock_gh.return_value = "[{\"number\": 1}]" # 1 ready issue
        mock_agent_call.return_value = "success"
        
        so.spawn_coder_if_needed()
        
        self.assertTrue(mock_agent_call.called)
        mock_agent_call.assert_called_with('software-coder', 'Please take issue 1', run_async=True)

if __name__ == '__main__':
    unittest.main()
