import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root (2 levels deep)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.gog import gog

class TestGogTool(unittest.TestCase):

    @patch('tools.gog.os.path.exists')
    def test_gog_binary_not_found(self, mock_exists):
        mock_exists.return_value = False
        result = gog.func(command="calendar calendars")
        self.assertIn("Error: gog binary not found", result)

    @patch('tools.gog.os.path.exists')
    @patch('tools.gog.subprocess.run')
    def test_gog_success(self, mock_run, mock_exists):
        mock_exists.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = "calendar list"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = gog.func(command="calendar calendars")
        
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertTrue(called_cmd[0].endswith("bin/gog"))
        self.assertEqual(called_cmd[1:], ["calendar", "calendars"])
        self.assertEqual(result, "calendar list")

    @patch('tools.gog.os.path.exists')
    @patch('tools.gog.subprocess.run')
    def test_gog_with_arguments(self, mock_run, mock_exists):
        mock_exists.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = "events list"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = gog.func(command="calendar events primary --today")
        
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertEqual(called_cmd[1:], ["calendar", "events", "primary", "--today"])
        self.assertEqual(result, "events list")

    @patch('tools.gog.os.path.exists')
    @patch('tools.gog.subprocess.run')
    def test_gog_error_output(self, mock_run, mock_exists):
        mock_exists.return_value = True
        
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "error occurred"
        mock_result.returncode = 1
        mock_run.return_value = mock_result
        
        result = gog.func(command="calendar calendars")
        
        self.assertEqual(result, "error occurred")

    @patch('tools.gog.os.path.exists')
    @patch('tools.gog.subprocess.run')
    def test_gog_exception(self, mock_run, mock_exists):
        mock_exists.return_value = True
        mock_run.side_effect = Exception("cmd failed")
        
        result = gog.func(command="calendar calendars")
        
        self.assertIn("Error performing gog action: cmd failed", result)

if __name__ == '__main__':
    unittest.main()
