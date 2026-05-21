import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root (2 levels deep)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.bash import bash

class TestBashTool(unittest.TestCase):

    @patch('tools.bash.subprocess.run')
    def test_missing_agent_id(self, mock_run):
        result = bash.func(command_string="python test.py", cwd=".", agent_id="")
        self.assertIn("Error: agent_id is required", result)

    @patch('core.loaders.tools_loader.ToolsLoader.check_permission')
    def test_permission_denied(self, mock_check_permission):
        mock_check_permission.return_value = False
        
        result = bash.func(command_string="python test.py", cwd="/workspace", agent_id="test_agent")
        self.assertIn("Error: Agent test_agent does not have permission", result)

    @patch('core.loaders.tools_loader.ToolsLoader.check_permission')
    @patch('tools.bash.subprocess.run')
    def test_bash_success(self, mock_run, mock_check_permission):
        mock_check_permission.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = "script output"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = bash.func(command_string="python test.py", cwd="/workspace", agent_id="test_agent")
        
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertEqual(called_cmd, ["python", "test.py"])
        self.assertIn("script output", result)

    @patch('core.loaders.tools_loader.ToolsLoader.check_permission')
    @patch('tools.bash.subprocess.run')
    def test_bash_failure(self, mock_run, mock_check_permission):
        mock_check_permission.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "error message"
        mock_result.returncode = 1
        mock_run.return_value = mock_result
        
        result = bash.func(command_string="python test.py", cwd="/workspace", agent_id="test_agent")
        
        self.assertIn("Error code 1", result)
        self.assertIn("error message", result)

    def test_bash_empty_command(self):
        result = bash.func(command_string="", cwd="/workspace", agent_id="test_agent")
        self.assertIn("Error: command_string is required", result)

if __name__ == '__main__':
    unittest.main()
