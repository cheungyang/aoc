"""The scheduled entry point.

The contract that matters here is the one the cron depends on: nothing to do
means no output and exit 0, a broken configuration means a message and exit 1.
Getting that backwards either floods a channel every five minutes or hides a
pipeline that has silently stopped.
"""
import contextlib
import importlib.util
import io
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
        self.cache_path = os.path.join(self.tmp.name, "tick_errors.json")
        patcher = patch.dict(
            os.environ, {"AOC_TICK_ERROR_CACHE": self.cache_path}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
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


class TestOnlyTheOutcomeReachesStdout(TickScriptTestCase):
    """stdout is the message. Everything else the runtime says is not.

    The runtime narrates itself on stdout — graph reloads, tool rosters, every
    tool call the worker makes. The script-executor posts this script's stdout
    to `#software-dev` verbatim, so all of it became the message and the one
    line that mattered was buried at the bottom of it.
    """

    def _tick_that_narrates(self, report):
        async def run_tick(_manifest_path):
            print("GraphsLoader: Loaded/Reloaded graph 'coding'")
            print("Loaded 2 tools for graph-worker: ['bash', 'filesystem']")
            print('[Agent:graph-worker] Tool use: filesystem [action "ls" on .]')
            return report
        return run_tick

    def test_runtime_chatter_never_reaches_stdout(self):
        self.write([])
        buffer = io.StringIO()

        with patch.object(self.module, "run_tick", self._tick_that_narrates("🧠 `T1`: implemented.")), \
             contextlib.redirect_stdout(buffer):
            code = self.run_main()

        self.assertEqual(code, 0)
        self.assertEqual(buffer.getvalue().strip(), "🧠 `T1`: implemented.")

    def test_a_tick_with_nothing_to_report_stays_completely_silent(self):
        """Chatter must not turn an idle tick into a message 288 times a day."""
        self.write([])
        buffer = io.StringIO()

        with patch.object(self.module, "run_tick", self._tick_that_narrates("")), \
             contextlib.redirect_stdout(buffer):
            self.run_main()

        self.assertEqual(buffer.getvalue(), "")

    def test_verbose_keeps_the_chatter_on_stderr(self):
        """Debuggability is not lost, it is just moved off the channel's path."""
        self.write([])
        out, err = io.StringIO(), io.StringIO()

        with patch.object(self.module, "run_tick", self._tick_that_narrates("done")), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.run_main("--verbose")

        self.assertEqual(out.getvalue().strip(), "done")
        self.assertIn("Loaded/Reloaded graph", err.getvalue())


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


class TestEveryProjectGetsATick(unittest.TestCase):
    """Each project owns a queue, so the cron — which names no project — has to
    visit all of them, and say which one each line came from."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.module = _load_module()
        self.manifests = [
            self.write_project("alpha", ["alpha task"]),
            self.write_project("beta", ["beta task"]),
        ]

    def write_project(self, name, queue):
        folder = os.path.join(self.tmp.name, name)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "build_request.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": "3.0", "project_name": name, "queue": []}, f)
        return path

    def run_main(self, *argv, reports=None):
        """Runs the script with discovery pointed at the temp tree."""
        reports = reports if reports is not None else {}

        def _tick(manifest_path, args):
            return reports.get(os.path.basename(os.path.dirname(manifest_path)), "")

        with patch.object(sys, "argv", ["coding_tick.py", *argv]), \
             patch.object(self.module, "tick_project", side_effect=_tick), \
             patch("graphs.coding.utils.dag.discover_manifests", return_value=self.manifests), \
             patch("builtins.print") as mock_print:
            code = self.module.main()
        return code, mock_print

    def test_with_no_project_named_every_queue_is_visited(self):
        code, printed = self.run_main(reports={"alpha": "did a thing", "beta": "did another"})

        self.assertEqual(code, 0)
        output = printed.call_args[0][0]
        self.assertIn("did a thing", output)
        self.assertIn("did another", output)

    def test_each_line_says_which_project_it_came_from(self):
        """Two projects reporting into one channel is unreadable otherwise."""
        _, printed = self.run_main(reports={"alpha": "did a thing", "beta": "did another"})

        output = printed.call_args[0][0]
        self.assertIn("alpha", output)
        self.assertIn("beta", output)

    def test_a_quiet_project_contributes_nothing(self):
        _, printed = self.run_main(reports={"beta": "did another"})

        output = printed.call_args[0][0]
        self.assertNotIn("alpha", output)
        self.assertIn("did another", output)

    def test_all_queues_idle_stays_completely_silent(self):
        code, printed = self.run_main(reports={})

        self.assertEqual(code, 0)
        printed.assert_not_called()

    def test_naming_one_project_leaves_the_others_alone(self):
        seen = []

        with patch.object(sys, "argv", ["coding_tick.py", "--manifest", self.manifests[0]]), \
             patch.object(self.module, "tick_project",
                          side_effect=lambda p, a: seen.append(p) or ""), \
             patch("builtins.print"):
            self.module.main()

        self.assertEqual(seen, [self.manifests[0]])

    def test_no_projects_at_all_is_not_an_error(self):
        """An empty `pkm/wiki/software` means nothing has been queued yet."""
        self.manifests = []
        code, printed = self.run_main()

        self.assertEqual(code, 0)
        printed.assert_not_called()


class TestErrorCaching(TickScriptTestCase):
    def test_first_occurrence_of_error_is_reported(self):
        self.write([])
        error_msg = "🛑 Coding tick error: Preflight failed: Token not found"
        with patch.object(self.module, "run_tick", return_value=error_msg), \
             patch("builtins.print") as mock_print:
            code = self.run_main()

        self.assertEqual(code, 0)
        mock_print.assert_called_once_with(error_msg)

    def test_repeating_same_error_is_suppressed(self):
        self.write([])
        error_msg = "🛑 Coding tick error: Preflight failed: Token not found"
        with patch.object(self.module, "run_tick", return_value=error_msg), \
             patch("builtins.print") as mock_print:
            code1 = self.run_main()
            self.assertEqual(code1, 0)
            mock_print.assert_called_once_with(error_msg)

        # Second tick returns the exact same error; it must be suppressed.
        with patch.object(self.module, "run_tick", return_value=error_msg), \
             patch("builtins.print") as mock_print2:
            code2 = self.run_main()
            self.assertEqual(code2, 0)
            mock_print2.assert_not_called()

    def test_different_error_is_reported_when_error_changes(self):
        self.write([])
        error1 = "🛑 Coding tick error: Preflight failed: Token not found"
        error2 = "🛑 Coding tick error: Setup command failed (exit 127)"
        with patch.object(self.module, "run_tick", return_value=error1), \
             patch("builtins.print") as mock_print:
            self.run_main()
            mock_print.assert_called_once_with(error1)

        # Different error is not suppressed.
        with patch.object(self.module, "run_tick", return_value=error2), \
             patch("builtins.print") as mock_print2:
            self.run_main()
            mock_print2.assert_called_once_with(error2)

    def test_successful_tick_clears_error_cache(self):
        self.write([])
        error = "🛑 Coding tick error: Preflight failed: Token not found"
        with patch.object(self.module, "run_tick", return_value=error), \
             patch("builtins.print") as mock_print:
            self.run_main()
            mock_print.assert_called_once_with(error)

        # Successful tick clears error cache.
        with patch.object(self.module, "run_tick",
                          return_value="🧠 T1: implemented."), \
             patch("builtins.print") as mock_print2:
            self.run_main()
            mock_print2.assert_called_once_with("🧠 T1: implemented.")

        # Error occurs again later; now reported again because cache cleared.
        with patch.object(self.module, "run_tick", return_value=error), \
             patch("builtins.print") as mock_print3:
            self.run_main()
            mock_print3.assert_called_once_with(error)

    def test_no_cache_flag_bypasses_suppression(self):
        self.write([])
        error = "🛑 Coding tick error: Preflight failed: Token not found"
        with patch.object(self.module, "run_tick", return_value=error), \
             patch("builtins.print") as mock_print:
            self.run_main()
            mock_print.assert_called_once_with(error)

        # Running with --no-cache reports the error even if it persists.
        with patch.object(self.module, "run_tick", return_value=error), \
             patch("builtins.print") as mock_print2:
            self.run_main("--no-cache")
            mock_print2.assert_called_once_with(error)

    def test_clear_cache_flag_clears_stored_error(self):
        self.write([])
        error = "🛑 Coding tick error: Preflight failed: Token not found"
        with patch.object(self.module, "run_tick", return_value=error), \
             patch("builtins.print"):
            self.run_main()

        # Running with --clear-cache clears stored error and reports.
        with patch.object(self.module, "run_tick", return_value=error), \
             patch("builtins.print") as mock_print2:
            self.run_main("--clear-cache")
            mock_print2.assert_called_once_with(error)

    def test_dry_run_does_not_mutate_error_cache(self):
        self.write([{
            "task_id": "T1", "status": "pending", "stage": "queued",
            "dependencies": [], "verification_command": "pytest -q"
        }])
        error = "🛑 Coding tick error: Preflight failed: Token not found"
        with patch.object(self.module, "run_tick", return_value=error), \
             patch("builtins.print"):
            self.run_main()

        # Dry run does not clear or affect cache.
        with patch("builtins.print"):
            self.run_main("--dry-run")

        # Second normal tick still suppresses the persisting error.
        with patch.object(self.module, "run_tick", return_value=error), \
             patch("builtins.print") as mock_print:
            self.run_main()
            mock_print.assert_not_called()

    def test_multi_project_error_caching_isolates_projects(self):
        project1 = os.path.join(self.tmp.name, "p1", "build_request.json")
        project2 = os.path.join(self.tmp.name, "p2", "build_request.json")
        os.makedirs(os.path.dirname(project1), exist_ok=True)
        os.makedirs(os.path.dirname(project2), exist_ok=True)
        with open(project1, "w") as f:
            json.dump({"project_name": "p1", "queue": []}, f)
        with open(project2, "w") as f:
            json.dump({"project_name": "p2", "queue": []}, f)

        err = "🛑 Coding tick error: Preflight failed: Token not found"
        args1 = self.module.parse_args(["--manifest", project1])
        args2 = self.module.parse_args(["--manifest", project2])

        with patch.object(self.module, "run_tick", return_value=err):
            # First tick for project 1 reports the error
            r1 = self.module.tick_project(project1, args1)
            self.assertEqual(r1, err)
            # Second tick for project 1 suppresses the error
            r2 = self.module.tick_project(project1, args1)
            self.assertEqual(r2, "")
            # Project 2 is independent: its first tick still reports
            r3 = self.module.tick_project(project2, args2)
            self.assertEqual(r3, err)


class TestHasWork(TickScriptTestCase):
    """The scheduler asks this in-process before spawning a tick at all."""

    def has_work(self, manifests):
        with patch("graphs.coding.utils.dag.discover_manifests", return_value=manifests):
            return self.module.has_work(None)

    def test_schedulable_by_the_scheduler(self):
        from core.scheduler.script_runner import check_script
        self.assertEqual(check_script("coding_tick.py"), [])

    def test_no_projects_means_no_work(self):
        self.assertFalse(self.has_work([]))

    def test_idle_queue_means_no_work(self):
        self.write([{"task_id": "T1", "status": "done", "stage": "merged", "dependencies": []}])
        self.assertFalse(self.has_work([self.manifest_path]))

    def test_runnable_task_is_work(self):
        self.write([{
            "task_id": "T1", "status": "pending", "stage": "queued",
            "dependencies": [], "verification_command": "pytest -q"
        }])
        self.assertTrue(self.has_work([self.manifest_path]))

    def test_expired_lease_is_work(self):
        self.write([{
            "task_id": "T1", "status": "active", "stage": "implementing",
            "dependencies": [], "lease_owner": "tick_dead", "lease_expires_at": 1.0
        }])
        self.assertTrue(self.has_work([self.manifest_path]))

    def test_unreadable_manifest_is_work_so_the_tick_reports_it(self):
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertTrue(self.has_work([self.manifest_path]))

    def test_import_does_not_change_directory(self):
        cwd = os.getcwd()
        _load_module()
        self.assertEqual(os.getcwd(), cwd)


if __name__ == "__main__":
    unittest.main()
