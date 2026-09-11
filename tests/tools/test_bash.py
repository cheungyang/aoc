import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root (2 levels deep)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.bash import bash
from tests.helpers import execution_context

class TestBashTool(unittest.TestCase):

    def _run(self, command_string, cwd, agent_id="test_agent"):
        """Invokes the bash tool inside an ambient ExecutionContext for `agent_id`."""
        with execution_context(agent_id=agent_id):
            return bash.func(command_string=command_string, cwd=cwd)

    @patch('tools.bash.subprocess.run')
    def test_missing_execution_context(self, mock_run):
        # No ambient ExecutionContext: the tool must refuse to run and must not shell out.
        result = bash.func(command_string="python test.py", cwd=".")
        self.assertIn("Error: no active execution context; this tool must be called from an agent run.", result)
        self.assertFalse(mock_run.called)

    @patch('core.loaders.tools_loader.ToolsLoader.check_permission')
    def test_permission_denied(self, mock_check_permission):
        mock_check_permission.return_value = False
        
        result = self._run(command_string="python test.py", cwd="/workspace")
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
        
        result = self._run(command_string="python test.py", cwd="/workspace")
        
        # Identity now flows through the ExecutionContext passed as the first argument.
        call_args = mock_check_permission.call_args
        self.assertEqual(call_args[0][0].agent_id, "test_agent")
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
        
        result = self._run(command_string="python test.py", cwd="/workspace")
        
        self.assertIn("Error code 1", result)
        self.assertIn("error message", result)

    def test_bash_empty_command(self):
        result = self._run(command_string="", cwd="/workspace")
        self.assertIn("Error: command_string is required", result)

    @patch('core.loaders.tools_loader.ToolsLoader.check_permission')
    @patch('tools.bash.subprocess.run')
    def test_bash_large_output_truncation(self, mock_run, mock_check_permission):
        mock_check_permission.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = "X" * 30000
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = self._run(command_string="cat bigfile.txt", cwd="/workspace")
        self.assertIn("Output truncated: 20000 characters omitted", result)
        self.assertIn("Total length was 30000 characters", result)


if __name__ == '__main__':
    unittest.main()
