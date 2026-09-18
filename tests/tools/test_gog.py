import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root (2 levels deep)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.gog import gog
from core.util import format_tool_response

class TestGogTool(unittest.TestCase):

    @patch('tools.gog.os.path.exists')
    def test_gog_binary_not_found(self, mock_exists):
        mock_exists.return_value = False
        result = gog.func(command="calendar calendars")
        self.assertIn("Error: gog binary not found", result)

    @patch('tools.gog.os.path.exists')
    @patch('tools.gog.subprocess.run')
    def test_gog_fallback_success(self, mock_run, mock_exists):
        # First call (workspace) returns False, second call (usr/local) returns True
        mock_exists.side_effect = [False, True]
        
        mock_result = MagicMock()
        mock_result.stdout = "calendar list"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        result = gog.func(command="calendar calendars")
        
        self.assertTrue(mock_run.called)
        called_cmd = mock_run.call_args[0][0]
        self.assertEqual(called_cmd[0], "/usr/local/bin/gog")
        self.assertEqual(called_cmd[1:], ["calendar", "calendars"])
        self.assertEqual(result, format_tool_response("gog", payload="calendar list", errors="None"))

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
        self.assertEqual(result, format_tool_response("gog", payload="calendar list", errors="None"))

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
        self.assertEqual(result, format_tool_response("gog", payload="events list", errors="None"))

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
        
        self.assertEqual(result, format_tool_response("gog", payload="error occurred", errors="None"))

    @patch('tools.gog.os.path.exists')
    @patch('tools.gog.subprocess.run')
    def test_gog_exception(self, mock_run, mock_exists):
        mock_exists.return_value = True
        mock_run.side_effect = Exception("cmd failed")
        
        result = gog.func(command="calendar calendars")
        
        self.assertIn("Error performing gog action: cmd failed", result)

    @patch('tools.gog.os.path.exists')
    @patch('tools.gog.subprocess.run')
    def test_gog_timeout(self, mock_run, mock_exists):
        import subprocess
        mock_exists.return_value = True
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["gog", "calendar"], timeout=30.0)
        
        result = gog.func(command="calendar calendars")
        self.assertIn("Error: gog command timed out after 30 seconds", result)

    @patch('tools.gog.os.path.exists')
    @patch('tools.gog.subprocess.run')
    def test_gog_passes_timeout_and_devnull(self, mock_run, mock_exists):
        import subprocess
        mock_exists.return_value = True
        mock_result = MagicMock()
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        gog.func(command="calendar calendars")
        self.assertTrue(mock_run.called)
        kwargs = mock_run.call_args[1]
        self.assertEqual(kwargs.get("timeout"), 30.0)
        self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)

    @patch('tools.gog.os.path.exists')
    @patch('tools.gog.subprocess.run')
    def test_gog_passes_keyring_env(self, mock_run, mock_exists):
        mock_exists.return_value = True
        mock_result = MagicMock()
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        with patch.dict(os.environ, {"GOG_KEYRING_BACKEND": "file", "GOG_KEYRING_PASSWORD": "test_password"}, clear=False):
            gog.func(command="calendar calendars")
            self.assertTrue(mock_run.called)
            kwargs = mock_run.call_args[1]
            env = kwargs.get("env")
            self.assertIsNotNone(env)
            self.assertEqual(env.get("GOG_KEYRING_BACKEND"), "file")
            self.assertEqual(env.get("GOG_KEYRING_PASSWORD"), "test_password")

if __name__ == '__main__':
    unittest.main()
