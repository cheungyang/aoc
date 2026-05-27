import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import json
import importlib.util
import datetime

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# Import orchestrator_cron.py
spec = importlib.util.spec_from_file_location("orchestrator_cron", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "orchestrator_cron.py")))
oc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oc)

class TestOrchestratorCron(unittest.TestCase):

    @patch.object(oc, 'gh')
    def test_run_gh_tool_success(self, mock_gh):
        mock_gh.invoke.return_value = "<payload>my data</payload>"
        result = oc.run_gh_tool("issue list")
        self.assertEqual(result, "my data")

    @patch.object(oc, 'run_gh_tool')
    def test_check_github_has_work_pr(self, mock_run_gh):
        mock_run_gh.side_effect = ["open prs", ""]
        result = oc.check_github()
        self.assertTrue(result)

    @patch.object(oc, 'run_gh_tool')
    def test_check_github_has_work_issue(self, mock_run_gh):
        mock_run_gh.side_effect = ["", "open issues"]
        result = oc.check_github()
        self.assertTrue(result)

    @patch.object(oc, 'run_gh_tool')
    def test_check_github_no_work(self, mock_run_gh):
        mock_run_gh.side_effect = ["", ""]
        result = oc.check_github()
        self.assertFalse(result)

    @patch.object(oc, 'run_job_list_tool')
    def test_check_watchdog_stuck_job(self, mock_job_list):
        two_hours_ago = datetime.datetime.now() - datetime.timedelta(hours=2)
        started_str = two_hours_ago.strftime('%Y-%m-%d %H:%M:%S')
        mock_job_list.return_value = f"[{{'job_id': '1', 'agent_id': 'agent1', 'started': '{started_str}', 'status': 'running'}}]"
        
        result = oc.check_watchdog()
        self.assertTrue(result)

    @patch.object(oc, 'run_job_list_tool')
    def test_check_watchdog_no_stuck_job(self, mock_job_list):
        ten_mins_ago = datetime.datetime.now() - datetime.timedelta(minutes=10)
        started_str = ten_mins_ago.strftime('%Y-%m-%d %H:%M:%S')
        mock_job_list.return_value = f"[{{'job_id': '1', 'agent_id': 'agent1', 'started': '{started_str}', 'status': 'running'}}]"
        
        result = oc.check_watchdog()
        self.assertFalse(result)

    @patch.object(oc, 'run_job_list_tool')
    def test_check_watchdog_job_not_running(self, mock_job_list):
        two_hours_ago = datetime.datetime.now() - datetime.timedelta(hours=2)
        started_str = two_hours_ago.strftime('%Y-%m-%d %H:%M:%S')
        mock_job_list.return_value = f"[{{'job_id': '1', 'agent_id': 'agent1', 'started': '{started_str}', 'status': 'completed'}}]"
        
        result = oc.check_watchdog()
        self.assertFalse(result)

    @patch.object(oc, 'check_github')
    @patch.object(oc, 'check_watchdog')
    @patch.object(oc, 'run_agent_call_tool')
    def test_main_wakes_up_agent(self, mock_agent_call, mock_watchdog, mock_gh):
        mock_gh.return_value = True
        mock_watchdog.return_value = False
        mock_agent_call.return_value = "success"
        
        oc.main()
        
        self.assertTrue(mock_agent_call.called)
        mock_agent_call.assert_called_with('main', 'Execute the software_orchestration skill.', run_async=True)

    @patch.object(oc, 'check_github')
    @patch.object(oc, 'check_watchdog')
    @patch.object(oc, 'run_agent_call_tool')
    def test_main_does_not_wake_up_agent(self, mock_agent_call, mock_watchdog, mock_gh):
        mock_gh.return_value = False
        mock_watchdog.return_value = False
        
        oc.main()
        
        self.assertFalse(mock_agent_call.called)

if __name__ == '__main__':
    unittest.main()
