"""The runner contract: a command's verdict and a runner's failure stay separate.

Everything downstream depends on that split. If an infrastructure failure could
look like `passed`, broken code would be published; if it could look like a
test failure, the worker would rewrite correct code chasing a network error.
"""
import unittest

from graphs.coding.utils.sandbox.runner_types import RunResult, Step, VerifyRunner
from graphs.coding.utils.sandbox.local_runner import LocalRunner


class TestRunResult(unittest.TestCase):
    def test_exit_zero_without_infra_error_passes(self):
        self.assertTrue(RunResult(0, "ok", "").passed)

    def test_non_zero_exit_does_not_pass(self):
        self.assertFalse(RunResult(1, "", "boom").passed)

    def test_an_infra_error_never_passes_even_with_exit_zero(self):
        """A runner that died before reporting must not read as green."""
        self.assertFalse(RunResult(0, "", "", infra_error="sandbox did not start").passed)

    def test_as_tuple_matches_the_run_in_worktree_shape(self):
        self.assertEqual(RunResult(2, "out", "err").as_tuple(), (2, "out", "err"))


class TestStep(unittest.TestCase):
    def test_steps_are_immutable(self):
        step = Step("verify", "pytest")
        with self.assertRaises(Exception):
            step.command = "rm -rf /"  # type: ignore[misc]


class TestProtocol(unittest.TestCase):
    def test_local_runner_satisfies_the_protocol(self):
        self.assertIsInstance(LocalRunner(), VerifyRunner)


if __name__ == "__main__":
    unittest.main()
