import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root (2 levels deep)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.gh import gh

class TestGhTool(unittest.TestCase):

    @patch('tools.gh.subprocess.run')
    def test_missing_agent_id(self, mock_run):
        result = gh.func(action="view", agent_id="", session_id="123")
        self.assertIn("Error: agent_id is required", result)

    @patch('core.loaders.tools_loader.ToolsLoader.check_permission')
    def test_permission_denied(self, mock_check_permission):
        mock_check_permission.return_value = False
        
        result = gh.func(action="create", agent_id="test_agent", task_description="test task")
        self.assertIn("Error: Agent test_agent does not have permission", result)

    @patch('core.loaders.tools_loader.ToolsLoader.check_permission')
    @patch('tools.gh.subprocess.run')
    def test_gh_create_success(self, mock_run, mock_check_permission):
        mock_check_permission.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = "task created"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = gh.func(action="create", agent_id="test_agent", task_description="test task")
        
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertEqual(called_cmd, ["gh", "agent-task", "create", "test task"])
        self.assertIn("Create task result:\ntask created", result)

    @patch('core.loaders.tools_loader.ToolsLoader.check_permission')
    @patch('tools.gh.subprocess.run')
    def test_gh_create_with_flags(self, mock_run, mock_check_permission):
        mock_check_permission.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = "task created"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = gh.func(action="create", agent_id="test_agent", task_description="test task", flags="--repo owner/repo")
        
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertEqual(called_cmd, ["gh", "agent-task", "create", "test task", "--repo", "owner/repo"])

    @patch('core.loaders.tools_loader.ToolsLoader.check_permission')
    @patch('tools.gh.subprocess.run')
    def test_gh_create_failure(self, mock_run, mock_check_permission):
        mock_check_permission.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "error message"
        mock_result.returncode = 1
        mock_run.return_value = mock_result
        
        result = gh.func(action="create", agent_id="test_agent", task_description="test task")
        
        self.assertIn("Error code 1", result)
        self.assertIn("error message", result)

    @patch('core.loaders.tools_loader.ToolsLoader.check_permission')
    @patch('tools.gh.subprocess.run')
    def test_gh_view_success(self, mock_run, mock_check_permission):
        mock_check_permission.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = "task details"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = gh.func(action="view", agent_id="test_agent", session_id="123")
        
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertEqual(called_cmd, ["gh", "agent-task", "view", "123"])
        self.assertIn("View task result:\ntask details", result)

    @patch('core.loaders.tools_loader.ToolsLoader.check_permission')
    @patch('tools.gh.subprocess.run')
    def test_gh_view_with_flags(self, mock_run, mock_check_permission):
        mock_check_permission.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = "task details"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = gh.func(action="view", agent_id="test_agent", session_id="123", flags="--log")
        
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertEqual(called_cmd, ["gh", "agent-task", "view", "123", "--log"])

if __name__ == '__main__':
    unittest.main()
