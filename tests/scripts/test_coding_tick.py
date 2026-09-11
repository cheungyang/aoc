"""The scheduled entry point.

The contract that matters here is the one the cron depends on: nothing to do
means no output and exit 0, a broken configuration means a message and exit 1.
Getting that backwards either floods a channel every five minutes or hides a
pipeline that has silently stopped.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT_PATH = os.path.join(PROJECT_ROOT, "scripts", "coding_tick.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("coding_tick", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TickScriptTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manifest_path = os.path.join(self.tmp.name, "build_request.json")
        self.module = _load_module()

    def write(self, queue):
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump({"version": "3.0", "project_name": "demo", "queue": queue}, f)

    def run_main(self, *argv):
        with patch.object(sys, "argv", ["coding_tick.py", "--manifest", self.manifest_path, *argv]):
            return self.module.main()


class TestExitCodes(TickScriptTestCase):
    def test_a_missing_manifest_is_not_an_error(self):
        """Nothing has been queued yet; alerting on that every five minutes is noise."""
        os.remove(self.manifest_path) if os.path.exists(self.manifest_path) else None
        self.assertEqual(self.run_main(), 0)

    def test_an_idle_queue_prints_nothing_and_exits_zero(self):
        self.write([])
        with patch("builtins.print") as mock_print:
            code = self.run_main()

        self.assertEqual(code, 0)
        mock_print.assert_not_called()

    def test_a_broken_manifest_is_loud_and_exits_one(self):
        """Silence here would be indistinguishable from an idle queue."""
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            f.write("{not json")

        with patch("sys.stderr"):
            code = self.run_main()

        self.assertEqual(code, 1)

    def test_a_report_is_printed_when_the_tick_did_something(self):
        self.write([])
        with patch.object(self.module, "run_tick", return_value="AOC-01: published PR #7"), \
             patch("builtins.print") as mock_print:
            code = self.run_main()

        self.assertEqual(code, 0)
        mock_print.assert_called_once_with("AOC-01: published PR #7")


class TestDryRun(TickScriptTestCase):
    def test_dry_run_names_the_task_and_the_route(self):
        self.write([{
            "task_id": "T1", "status": "pending", "stage": "queued",
            "dependencies": [], "verification_command": "pytest -q"
        }])

        with patch("builtins.print") as mock_print:
            code = self.run_main("--dry-run")

        self.assertEqual(code, 0)
        printed = mock_print.call_args[0][0]
        self.assertIn("T1", printed)
        self.assertIn("implement", printed)

    def test_dry_run_is_silent_with_nothing_to_do(self):
        self.write([{"task_id": "T1", "status": "done", "stage": "done", "dependencies": []}])

        with patch("builtins.print") as mock_print:
            self.run_main("--dry-run")

        mock_print.assert_not_called()

    def test_dry_run_writes_nothing(self):
        self.write([{
            "task_id": "T1", "status": "pending", "stage": "queued",
            "dependencies": [], "verification_command": "pytest -q"
        }])
        before = open(self.manifest_path, encoding="utf-8").read()

        with patch("builtins.print"):
            self.run_main("--dry-run")

        self.assertEqual(open(self.manifest_path, encoding="utf-8").read(), before)


class TestMaxTasks(TickScriptTestCase):
    def test_ticking_stops_as_soon_as_a_tick_reports_nothing(self):
        """An idle tick means there is no more work; repeating it would be pointless."""
        self.write([])
        with patch.object(self.module, "run_tick", side_effect=["did a thing", ""]) as mock_tick, \
             patch("builtins.print"):
            self.run_main("--max-tasks", "5")

        self.assertEqual(mock_tick.call_count, 2)


class TestInvokedAsTheCronDoes(unittest.TestCase):
    """Executed as a file, the way the script-executor runs it."""

    def test_the_script_is_executable_and_silent_on_an_idle_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = os.path.join(tmp, "build_request.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({"version": "3.0", "project_name": "demo", "queue": []}, f)

            result = subprocess.run(
                [SCRIPT_PATH, "--manifest", manifest_path],
                capture_output=True, text=True, cwd=PROJECT_ROOT
            )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
