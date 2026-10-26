import subprocess
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root (2 levels deep)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools import gog as gog_module
from tools.gog import GogError, gog
from core.tooling import format_tool_response

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

    @patch('tools.gog.os.path.exists')
    @patch('tools.gog.subprocess.run')
    def test_gog_passes_keyring_password_from_config(self, mock_run, mock_exists):
        mock_exists.return_value = True
        mock_result = MagicMock()
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        from core.config.settings import Config
        cfg = Config()
        cfg.gog_keyring_password = "configured_password"

        with patch.dict(os.environ, {"GOG_KEYRING_BACKEND": "file"}, clear=False):
            os.environ.pop("GOG_KEYRING_PASSWORD", None)
            gog.func(command="calendar calendars")
            self.assertTrue(mock_run.called)
            kwargs = mock_run.call_args[1]
            env = kwargs.get("env")
            self.assertEqual(env.get("GOG_KEYRING_PASSWORD"), "configured_password")


    @patch('tools.gog.os.path.exists')
    @patch('tools.gog.subprocess.run')
    def test_gog_includes_hint_on_key_unwrap_failure(self, mock_run, mock_exists):
        mock_exists.return_value = True
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "open keyring: aes.KeyUnwrap(): integrity check failed"
        mock_result.returncode = 1
        mock_run.return_value = mock_result

        result = gog.func(command="calendar calendars")
        self.assertIn("aes.KeyUnwrap(): integrity check failed", result)
        self.assertIn("Gog Tool Hint", result)
        self.assertIn("GOG_KEYRING_PASSWORD in .env", result)

# The helpers calendar code uses: run_json, list_events, create_event, gog_binary.

def completed(stdout="", stderr="", returncode=0):
    result = MagicMock()
    result.stdout, result.stderr, result.returncode = stdout, stderr, returncode
    return result


@patch("tools.gog.gog_binary", return_value="/usr/local/bin/gog")
@patch("tools.gog.subprocess.run")
class TestRunJson(unittest.TestCase):
    """Every test stubs subprocess.run: none may reach a real calendar."""

    def test_list_events_sends_utc_range_and_parses_json(self, run, _binary):
        run.return_value = completed('{"events": [], "nextPageToken": ""}')
        start = datetime(2026, 10, 17, 7, tzinfo=timezone.utc)
        end = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)
        self.assertEqual(gog_module.list_events(start, end, max_results=300), {"events": [], "nextPageToken": ""})
        self.assertEqual(run.call_args.args[0], [
            "/usr/local/bin/gog", "calendar", "events", "primary",
            "--from", "2026-10-17T07:00:00Z", "--to", "2026-11-02T08:00:00Z", "--max", "300", "--json",
        ])
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_create_event_args(self, run, _binary):
        run.return_value = completed('{"event": {"id": "abc"}}')
        gog_module.create_event("primary", "Street Fair", "2026-10-24T17:00:00Z", "2026-10-24T21:00:00Z", location="Springfield")
        self.assertEqual(run.call_args.args[0][1:], [
            "calendar", "create", "primary", "--summary", "Street Fair",
            "--from", "2026-10-24T17:00:00Z", "--to", "2026-10-24T21:00:00Z",
            "--location", "Springfield", "--json",
        ])

    def test_all_day_create_passes_the_flag(self, run, _binary):
        run.return_value = completed('{"event": {}}')
        gog_module.create_event("primary", "Camping", "2026-10-24", "2026-10-26", all_day=True)
        self.assertIn("--all-day", run.call_args.args[0])

    def test_failure_raises_with_stderr(self, run, _binary):
        run.return_value = completed(stderr="calendar not found", returncode=1)
        with self.assertRaisesRegex(GogError, "calendar not found"):
            gog_module.run_json(["calendar", "events", "nope"])

    def test_keyring_failure_gets_the_hint(self, run, _binary):
        run.return_value = completed(stderr="aes.KeyUnwrap(): integrity check failed", returncode=1)
        with self.assertRaisesRegex(GogError, "GOG_KEYRING_PASSWORD"):
            gog_module.run_json(["calendar", "events", "primary"])

    def test_non_json_output_raises(self, run, _binary):
        run.return_value = completed("ID  SUMMARY")
        with self.assertRaisesRegex(GogError, "no JSON"):
            gog_module.run_json(["calendar", "events", "primary"])

    def test_timeout_raises(self, run, _binary):
        run.side_effect = subprocess.TimeoutExpired(cmd="gog", timeout=30)
        with self.assertRaisesRegex(GogError, "timed out"):
            gog_module.run_json(["calendar", "events", "primary"])


class TestBinary(unittest.TestCase):

    @patch("tools.gog.os.path.exists", return_value=False)
    def test_missing_binary_raises(self, _exists):
        with self.assertRaisesRegex(GogError, "not found"):
            gog_module.gog_binary()

    @patch("tools.gog.os.path.exists", side_effect=[False, True])
    def test_falls_back_to_usr_local(self, _exists):
        self.assertEqual(gog_module.gog_binary(), "/usr/local/bin/gog")


if __name__ == '__main__':
    unittest.main()
