"""Tests for the coding_admin CLI.

The CLI has one job beyond argument parsing: turning outcomes into exit codes an
operator (or a script) can act on. 0 is done, 1 is broken, 2 is "you asked for
something that does not make sense" — and the last one must not print a
traceback, because a traceback reads like a bug in the tool.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "scripts"))

import coding_admin  # noqa: E402
from graphs.coding.utils.manifest import MANIFEST_VERSION  # noqa: E402


class CodingAdminTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "build_request.json")
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        patch("sys.stdout", self.stdout).start()
        patch("sys.stderr", self.stderr).start()
        self.addCleanup(patch.stopall)

    def write(self, tasks):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({
                "version": MANIFEST_VERSION,
                "project_name": "proj",
                "repo": {"mode": "self", "slug": "owner/repo"},
                "queue": tasks,
            }, f)

    def run_cli(self, *args):
        return coding_admin.main(["--manifest", self.path, *args])

    def out(self):
        return self.stdout.getvalue()

    def err(self):
        return self.stderr.getvalue()

    def read(self):
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)


class TestExitCodes(CodingAdminTestCase):
    def test_a_successful_command_exits_zero(self):
        self.write([{"task_id": "A", "status": "queued"}])

        self.assertEqual(self.run_cli("status"), 0)

    def test_a_missing_manifest_exits_one_and_says_where_it_looked(self):
        code = coding_admin.main(["--manifest", "/nope/build_request.json", "status"])

        self.assertEqual(code, 1)
        self.assertIn("/nope/build_request.json", self.err())

    def test_an_operator_mistake_exits_two_without_a_traceback(self):
        self.write([{"task_id": "A", "status": "queued"}])

        code = self.run_cli("retry", "GHOST")

        self.assertEqual(code, 2)
        self.assertIn("GHOST", self.err())
        self.assertNotIn("Traceback", self.err())

    def test_an_unexpected_failure_exits_one(self):
        self.write([{"task_id": "A", "status": "queued"}])

        with patch("graphs.coding.utils.control.status_report", side_effect=OSError("disk gone")):
            code = self.run_cli("status")

        self.assertEqual(code, 1)
        self.assertIn("disk gone", self.err())


class TestStatusCommand(CodingAdminTestCase):
    def test_status_prints_the_queue(self):
        self.write([{"task_id": "A", "status": "done"},
                    {"task_id": "B", "status": "queued"}])

        self.run_cli("status")

        self.assertIn("`A`", self.out())
        self.assertIn("`B`", self.out())

    def test_status_with_a_task_id_prints_only_that_task(self):
        self.write([{"task_id": "A", "status": "done"},
                    {"task_id": "B", "status": "queued"}])

        self.run_cli("status", "A")

        self.assertNotIn("`B`", self.out())


class TestMutatingCommands(CodingAdminTestCase):
    def test_retry_writes_the_manifest(self):
        self.write([{"task_id": "A", "status": "halted", "attempts": {"implement": 3}}])

        self.assertEqual(self.run_cli("retry", "A"), 0)
        self.assertEqual(self.read()["queue"][0]["status"], "queued")
        self.assertEqual(self.read()["queue"][0]["attempts"], {})

    def test_retry_passes_the_stage_through(self):
        self.write([{"task_id": "A", "status": "halted", "stage": "verified",
                     "impl_digest": "abc"}])

        self.assertEqual(self.run_cli("retry", "A", "--from-stage", "provisioned"), 0)
        self.assertEqual(self.read()["queue"][0]["stage"], "provisioned")
        self.assertIsNone(self.read()["queue"][0]["impl_digest"])

    def test_skip_then_unblock_round_trips(self):
        self.write([{"task_id": "A", "status": "queued"}])

        self.run_cli("skip", "A")
        self.assertEqual(self.read()["queue"][0]["status"], "blocked")

        self.run_cli("unblock", "A")
        self.assertEqual(self.read()["queue"][0]["status"], "queued")

    def test_abort_records_the_reason_given_on_the_command_line(self):
        self.write([{"task_id": "A", "status": "active"}])

        self.run_cli("abort", "A", "--reason", "spec was wrong")

        self.assertEqual(self.read()["queue"][0]["last_error"]["message"], "spec was wrong")


class TestResetConfirmation(CodingAdminTestCase):
    def test_reset_refuses_without_yes_when_there_is_no_terminal(self):
        # Under cron or a pipe there is nobody to answer the prompt, and
        # defaulting to "go ahead" would make reset the easiest thing to
        # trigger by accident.
        self.write([{"task_id": "A", "status": "queued", "run_id": "r1"}])

        with patch("sys.stdin.isatty", return_value=False), \
             patch("graphs.coding.utils.control.git_ops") as mock_git:
            code = self.run_cli("reset", "A")

        self.assertEqual(code, 0)
        self.assertIn("Cancelled", self.out())
        self.assertEqual(mock_git.mock_calls, [])

    def test_reset_shows_a_preview_before_asking(self):
        self.write([{"task_id": "A", "status": "queued", "run_id": "r1",
                     "branch_name": "feat/a"}])

        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="n"), \
             patch("graphs.coding.utils.control.git_ops"):
            self.run_cli("reset", "A")

        self.assertIn("Cancelled", self.out())

    def test_a_confirmed_reset_goes_ahead(self):
        self.write([{"task_id": "A", "status": "awaiting_review", "run_id": "r1",
                     "branch_name": "feat/a"}])

        with patch("sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="y"), \
             patch("graphs.coding.utils.control.git_ops.teardown_worktree",
                   new=AsyncMock(return_value=(True, ""))), \
             patch("graphs.coding.utils.control.git_ops.delete_branch",
                   new=AsyncMock(return_value=(True, ""))):
            code = self.run_cli("reset", "A")

        self.assertEqual(code, 0)
        self.assertEqual(self.read()["queue"][0]["status"], "queued")

    def test_yes_skips_the_prompt_entirely(self):
        self.write([{"task_id": "A", "status": "awaiting_review", "run_id": "r1"}])

        with patch("builtins.input", side_effect=AssertionError("should not prompt")), \
             patch("graphs.coding.utils.control.git_ops.teardown_worktree",
                   new=AsyncMock(return_value=(True, ""))):
            code = self.run_cli("reset", "A", "--yes")

        self.assertEqual(code, 0)

    def test_a_dry_run_needs_no_confirmation_and_changes_nothing(self):
        self.write([{"task_id": "A", "status": "awaiting_review", "run_id": "r1"}])

        with patch("builtins.input", side_effect=AssertionError("should not prompt")), \
             patch("graphs.coding.utils.control.git_ops") as mock_git:
            code = self.run_cli("reset", "A", "--dry-run")

        self.assertEqual(code, 0)
        self.assertEqual(mock_git.mock_calls, [])
        self.assertEqual(self.read()["queue"][0]["status"], "awaiting_review")


if __name__ == "__main__":
    unittest.main()
