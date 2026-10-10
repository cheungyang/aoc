"""The local backend behaves exactly like verify did before runners existed.

These run real subprocesses in a temp directory: the point of LocalRunner is
fidelity to `run_in_worktree`, which a mock would not prove.
"""
import os
import tempfile
import unittest

from graphs.coding.utils.sandbox.local_runner import LocalRunner
from graphs.coding.utils.sandbox.runner_types import Step


class TestLocalRunner(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ws = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    async def _run(self, steps, timeout=30.0):
        return await LocalRunner().run(steps, workspace_path=self.ws, timeout=timeout)

    async def test_a_passing_command_passes(self):
        result = await self._run([Step("verify", "echo hello")])

        self.assertTrue(result.passed)
        self.assertIn("hello", result.stdout)
        self.assertIsNone(result.failed_step)
        self.assertEqual(result.backend, "local")

    async def test_runs_in_the_workspace(self):
        with open(os.path.join(self.ws, "marker.txt"), "w") as fh:
            fh.write("x")

        result = await self._run([Step("verify", "test -f marker.txt")])

        self.assertTrue(result.passed)

    async def test_a_failing_command_reports_its_exit_code_and_stderr(self):
        result = await self._run([Step("verify", "echo nope >&2; exit 3")])

        self.assertEqual(result.exit_code, 3)
        self.assertIn("nope", result.stderr)
        self.assertEqual(result.failed_step, "verify")
        self.assertIsNone(result.infra_error, "a red test is not an infra failure")

    async def test_stops_at_the_first_failing_step(self):
        """A failed setup must not be followed by tests that would fail for its reasons."""
        result = await self._run([
            Step("setup", "exit 1"),
            Step("verify", "touch ran.txt"),
        ])

        self.assertEqual(result.failed_step, "setup")
        self.assertFalse(os.path.exists(os.path.join(self.ws, "ran.txt")))

    async def test_output_accumulates_across_steps(self):
        result = await self._run([
            Step("setup", "echo installed"),
            Step("verify", "echo tested; exit 1"),
        ])

        self.assertIn("installed", result.stdout)
        self.assertIn("tested", result.stdout)
        self.assertEqual(result.failed_step, "verify")

    async def test_a_missing_tool_reports_127(self):
        """`classify_failure` relies on this to recognise an environment problem."""
        result = await self._run([Step("verify", "definitely-not-a-real-binary-xyz")])

        self.assertEqual(result.exit_code, 127)

    async def test_timeout_is_a_budget_for_the_whole_run(self):
        result = await self._run([Step("verify", "sleep 5")], timeout=0.5)

        self.assertEqual(result.exit_code, 124)
        self.assertEqual(result.failed_step, "verify")

    async def test_no_steps_is_a_trivial_pass(self):
        self.assertTrue((await self._run([])).passed)


if __name__ == "__main__":
    unittest.main()
