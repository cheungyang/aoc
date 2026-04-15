import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root (2 levels deep)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.nlm import nlm
from core.util import format_tool_response

class TestNlmTool(unittest.TestCase):

    @patch('tools.nlm.os.path.exists')
    def test_nlm_binary_not_found(self, mock_exists):
        mock_exists.return_value = False
        result = nlm.func(command="notebook list")
        self.assertIn("Error: nlm binary not found", result)

    @patch('tools.nlm.os.path.exists')
    @patch('tools.nlm.subprocess.run')
    def test_nlm_success(self, mock_run, mock_exists):
        mock_exists.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = "notebook list output"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = nlm.func(command="notebook list")
        
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertTrue(called_cmd[0].endswith("bin/nlm"))
        self.assertEqual(called_cmd[1:], ["notebook", "list"])
        self.assertEqual(result, format_tool_response("nlm", payload="notebook list output", errors="None"))

    @patch('tools.nlm.os.path.exists')
    @patch('tools.nlm.subprocess.run')
    def test_nlm_with_arguments(self, mock_run, mock_exists):
        mock_exists.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = "notebook created"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = nlm.func(command="notebook create 'AI Research'")
        
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertEqual(called_cmd[1:], ["notebook", "create", "AI Research"])
        self.assertEqual(result, format_tool_response("nlm", payload="notebook created", errors="None"))

    @patch('tools.nlm.os.path.exists')
    @patch('tools.nlm.subprocess.run')
    def test_nlm_error_output(self, mock_run, mock_exists):
        mock_exists.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "error occurred"
        mock_result.returncode = 1
        mock_run.return_value = mock_result
        
        result = nlm.func(command="notebook list")
        
        self.assertEqual(result, format_tool_response("nlm", payload="error occurred", errors="None"))

    @patch('tools.nlm.os.path.exists')
    @patch('tools.nlm.subprocess.run')
    def test_nlm_exception(self, mock_run, mock_exists):
        mock_exists.return_value = True
        mock_run.side_effect = Exception("cmd failed")
        
        result = nlm.func(command="notebook list")
        
        self.assertIn("Error performing nlm action: cmd failed", result)

if __name__ == '__main__':
    unittest.main()
