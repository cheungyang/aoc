import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root (3 levels deep)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import tools.git.git_command as git_tool

class TestGitCommandTool(unittest.TestCase):

    @patch('tools.git.git_command.subprocess.run')
    def test_git_command_invokes_direct_git(self, mock_run):
        # 1. Mock execution loops outputs
        mock_result = MagicMock()
        mock_result.stdout = "Commit logs dumps"
        mock_result.stderr = "No errors"
        mock_run.return_value = mock_result

        # 2. Invoke isolated tool logic
        result = git_tool.git_command(args="log -n 5", path="./agents")

        # 3. Assertions
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertEqual(called_cmd[0], "git")
        self.assertEqual(called_cmd[1], "log")
        
        # Verify cwd presence attached
        kwargs = mock_run.call_args[1]
        self.assertTrue("cwd" in kwargs)
        self.assertTrue(kwargs["cwd"].endswith("/agents"))

        self.assertIn("Commit logs dumps", result)
        self.assertIn("No errors", result)

    def test_git_command_prevents_path_traversal(self):
        # Escape attempts sequence
        result = git_tool.git_command(args="status", path="../../etc")
        self.assertIn("Error: Path traversal access denied.", result)

if __name__ == "__main__":
    unittest.main()
