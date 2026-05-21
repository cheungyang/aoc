import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root (2 levels deep)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.git import git

class TestGitTool(unittest.TestCase):

    @patch('tools.git.subprocess.run')
    def test_git_command_execution(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = "output"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = git.func(command="status", path="/path/to/repo")
        
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        called_cwd = mock_run.call_args[1]['cwd']
        self.assertEqual(called_cmd, ["git", "status"])
        self.assertEqual(called_cwd, "/path/to/repo")
        self.assertIn("output", result)

    @patch('tools.git.subprocess.run')
    def test_git_command_with_quotes(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = "committed"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = git.func(command='commit -m "My Message"', path="/path/to/repo")
        
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertEqual(called_cmd, ["git", "commit", "-m", "My Message"])

    @patch('tools.git.subprocess.run')
    def test_git_command_failure_captured(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "error message"
        mock_result.returncode = 1
        mock_run.return_value = mock_result
        
        result = git.func(command="status", path="/path/to/repo")
        
        self.assertIn("error message", result)

if __name__ == '__main__':
    unittest.main()

