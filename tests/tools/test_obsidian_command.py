import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root (2 levels deep)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import tools.obsidian_command as obsidian_tool

class TestObsidianCommandTool(unittest.TestCase):

    @patch('tools.obsidian_command.subprocess.run')
    @patch('os.path.exists')
    def test_obsidian_command_invokes_cli(self, mock_exists, mock_run):
        mock_exists.return_value = True
        
        # 1. Mock execution outputs
        mock_result = MagicMock()
        mock_result.stdout = "Command output"
        mock_result.stderr = ""
        mock_run.return_value = mock_result
        
        # 2. Invoke tool
        result = obsidian_tool.obsidian_command(args="commands")
        
        # 3. Assertions
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertEqual(called_cmd[0], "/Applications/Obsidian.app/Contents/MacOS/obsidian")
        self.assertEqual(called_cmd[1], "commands")
        
        self.assertIn("Command output", result)

    @patch('os.path.exists')
    def test_obsidian_command_not_found(self, mock_exists):
        mock_exists.return_value = False
        
        result = obsidian_tool.obsidian_command(args="commands")
        
        self.assertIn("Error: Obsidian CLI not found", result)

if __name__ == "__main__":
    unittest.main()
