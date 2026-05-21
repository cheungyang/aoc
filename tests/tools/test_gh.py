import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root (2 levels deep)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.gh import gh

class TestGhTool(unittest.TestCase):

    @patch('tools.gh.subprocess.run')
    def test_gh_command_execution(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = "output"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = gh.func(command="issue list")
        
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertEqual(called_cmd, ["gh", "issue", "list"])
        self.assertIn("output", result)

    @patch('tools.gh.subprocess.run')
    def test_gh_command_with_quotes(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = "created"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = gh.func(command='issue create --title "My Title"')
        
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertEqual(called_cmd, ["gh", "issue", "create", "--title", "My Title"])

    @patch('tools.gh.subprocess.run')
    def test_gh_command_failure_captured(self, mock_run):
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "error message"
        mock_result.returncode = 1
        mock_run.return_value = mock_result
        
        result = gh.func(command="issue list")
        
        self.assertIn("error message", result)

if __name__ == '__main__':
    unittest.main()

