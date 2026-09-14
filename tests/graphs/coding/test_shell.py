"""The shared worktree command runner.

Both `setup` and `verify` decide what to do next from this function's return
value, so what it reports for a timeout or an unrunnable command is the input to
the environment-vs-code distinction the retry budget rests on.
"""
import os
import sys
import tempfile
import unittest

from graphs.coding.utils.shell import DEFAULT_TIMEOUT_SECONDS, run_in_worktree


class TestRunInWorktree(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    async def test_it_reports_the_exit_code_and_both_streams(self):
        code, out, err = await run_in_worktree(
            "echo hello; echo oops 1>&2; exit 3", cwd=self.tmp.name
        )

        self.assertEqual(code, 3)
        self.assertIn("hello", out)
        self.assertIn("oops", err)

    async def test_success_is_exit_zero(self):
        code, out, _ = await run_in_worktree("true", cwd=self.tmp.name)
        self.assertEqual(code, 0)

    async def test_the_command_runs_in_the_worktree_not_the_repo(self):
        """A test command run from the repo root would test the wrong code."""
        code, out, _ = await run_in_worktree("pwd", cwd=self.tmp.name)

        self.assertEqual(code, 0)
        self.assertEqual(
            os.path.realpath(out.strip()), os.path.realpath(self.tmp.name)
        )

    async def test_it_inherits_the_environment(self):
        """`npm` and `python` are only on PATH because the parent's env says so."""
        code, out, _ = await run_in_worktree(
            f"{sys.executable} -c 'print(1 + 1)'", cwd=self.tmp.name
        )

        self.assertEqual(code, 0)
        self.assertIn("2", out)

    async def test_a_missing_binary_is_a_failure_not_an_exception(self):
        """127 is what the caller classifies as a broken environment; a raised
        exception here would abort the whole tick instead of halting one task."""
        code, _, err = await run_in_worktree(
            "definitely-not-a-real-binary", cwd=self.tmp.name
        )

        self.assertEqual(code, 127)
        self.assertIn("not found", err.lower())

    async def test_a_hang_is_killed_and_reported_as_124(self):
        code, _, err = await run_in_worktree("sleep 30", cwd=self.tmp.name, timeout=0.2)

        self.assertEqual(code, 124)
        self.assertIn("Timed out", err)
        self.assertIn("sleep 30", err)

    async def test_a_directory_that_does_not_exist_never_raises(self):
        """Provisioning can fail after the path is computed; the caller must get
        a failure it can record, not a traceback."""
        code, _, err = await run_in_worktree(
            "true", cwd=os.path.join(self.tmp.name, "nope")
        )

        self.assertNotEqual(code, 0)
        self.assertTrue(err)

    def test_the_default_timeout_allows_a_cold_dependency_install(self):
        """The slowest legitimate thing either caller does. Two minutes was not
        enough, and a timeout tells the worker nothing it can act on."""
        self.assertGreaterEqual(DEFAULT_TIMEOUT_SECONDS, 600.0)


if __name__ == "__main__":
    unittest.main()
