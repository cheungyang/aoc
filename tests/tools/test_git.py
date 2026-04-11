import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root (2 levels deep)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.git import git

class TestGitTool(unittest.TestCase):

    @patch('tools.git.subprocess.run')
    def test_missing_agent_id(self, mock_run):
        result = git.func(action="log-p", path=".", agent_id="")
        self.assertIn("Error: agent_id is required", result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    def test_permission_denied(self, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = False
        
        result = git.func(action="log-p", path="/workspace/secret", agent_id="test_agent")
        self.assertIn("Error: Agent test_agent does not have permission", result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('tools.git.subprocess.run')
    def test_git_log_p_success(self, mock_run, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = "log output"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = git.func(action="log-p", path="/workspace/allowed_folder", agent_id="test_agent")
        
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertEqual(called_cmd, ["git", "log", "-p"])
        self.assertIn("Log result:\nlog output", result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('tools.git.subprocess.run')
    def test_git_pull_success(self, mock_run, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = "pull output"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = git.func(action="pull", path="/workspace/allowed_folder", agent_id="test_agent")
        
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertEqual(called_cmd, ["git", "pull", "-X", "theirs"])
        self.assertIn("Pull result:\npull output", result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('tools.git.subprocess.run')
    def test_git_push_success(self, mock_run, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = "success"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = git.func(action="push", path="/workspace/allowed_folder", agent_id="test_agent", message="feat: test")
        
        self.assertEqual(mock_run.call_count, 3)
        calls = mock_run.call_args_list
        self.assertEqual(calls[0][0][0], ["git", "pull", "-X", "theirs"])
        self.assertEqual(calls[1][0][0], ["git", "commit", "-am", "feat: test"])
        self.assertEqual(calls[2][0][0], ["git", "push", "origin", "main"])
        self.assertIn("Push process result:", result)


if __name__ == '__main__':
    unittest.main()
