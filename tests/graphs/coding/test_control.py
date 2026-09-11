"""Tests for the operator control plane.

The interesting properties here are not the strings these commands print, they
are the state they leave behind:

- `retry` must clear the attempt counters, or the very next tick re-halts;
- `retry --from-stage` must clear the digests, or the stage guards skip past the
  work you just asked to have redone;
- `reset` must clear *every* recorded field, because anything left behind makes
  the next run differ from a first run;
- nothing here may run the pipeline.
"""
import asyncio
import json
import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from graphs.coding.utils import control
from graphs.coding.utils.control import (
    RESET_FIELDS,
    ControlError,
    abort,
    reset,
    retry,
    skip,
    status_report,
    unblock,
)
from graphs.coding.utils.manifest import MANIFEST_VERSION, load_manifest


def _task(task_id="T1", **overrides):
    task = {
        "task_id": task_id,
        "feature_name": task_id,
        "status": "queued",
        "stage": "queued",
        "dependencies": [],
        "attempts": {},
    }
    task.update(overrides)
    return task


class ControlTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "build_request.json")

    def write(self, tasks, **manifest_overrides):
        manifest = {
            "version": MANIFEST_VERSION,
            "project_name": "proj",
            "repo": {"mode": "self", "slug": "owner/repo"},
            "queue": tasks,
        }
        manifest.update(manifest_overrides)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        return manifest

    def task(self, task_id="T1"):
        queue = load_manifest(self.path)["queue"]
        return next(t for t in queue if t["task_id"] == task_id)


class TestStatusReport(ControlTestCase):
    def test_an_empty_queue_says_so_rather_than_printing_a_header(self):
        self.write([])
        self.assertEqual(status_report(self.path), "The queue is empty.")

    def test_each_task_gets_a_line_with_its_status(self):
        self.write([_task("A", status="done"), _task("B", status="failed")])

        report = status_report(self.path)

        self.assertIn("`A`", report)
        self.assertIn("`B`", report)
        self.assertIn("done", report)
        self.assertIn("failed", report)

    def test_a_live_lease_is_shown_so_an_operator_knows_why_a_task_looks_stuck(self):
        self.write([_task("A", status="active", lease_owner="tick_9",
                          lease_expires_at=time.time() + 600)])

        report = status_report(self.path)

        self.assertIn("tick_9", report)

    def test_an_expired_lease_is_not_reported_as_a_claim(self):
        self.write([_task("A", status="active", lease_owner="tick_9",
                          lease_expires_at=time.time() - 1)])

        self.assertNotIn("tick_9", status_report(self.path))

    def test_the_last_error_is_surfaced_in_the_summary(self):
        self.write([_task("A", status="halted",
                          last_error={"kind": "git", "stage": "publish",
                                      "message": "push rejected"})])

        self.assertIn("push rejected", status_report(self.path))

    def test_asking_for_one_task_gives_the_detail_view(self):
        self.write([_task("A", pr_url="https://x/pull/3", branch_name="feat/a"),
                    _task("B")])

        report = status_report(self.path, "A")

        self.assertIn("https://x/pull/3", report)
        self.assertIn("feat/a", report)
        self.assertNotIn("`B`", report)

    def test_an_unknown_task_names_the_ones_that_do_exist(self):
        self.write([_task("A"), _task("B")])

        with self.assertRaises(ControlError) as ctx:
            status_report(self.path, "NOPE")

        self.assertIn("A", str(ctx.exception))
        self.assertIn("B", str(ctx.exception))


class TestRetry(ControlTestCase):
    def test_retry_clears_the_attempt_counters_that_caused_the_halt(self):
        self.write([_task("A", status="halted", stage="implemented",
                          attempts={"implement": 3})])

        retry(self.path, "A")

        task = self.task("A")
        self.assertEqual(task["attempts"], {})
        self.assertEqual(task["status"], "queued")

    def test_retry_preserves_the_stage_so_finished_work_is_not_redone(self):
        self.write([_task("A", status="halted", stage="verified",
                          impl_digest="abc", verified_digest="abc")])

        retry(self.path, "A")

        task = self.task("A")
        self.assertEqual(task["stage"], "verified")
        self.assertEqual(task["impl_digest"], "abc")

    def test_retry_clears_the_lease_so_the_next_tick_can_claim_it(self):
        self.write([_task("A", status="halted", lease_owner="dead_tick",
                          lease_expires_at=time.time() + 999)])

        retry(self.path, "A")

        task = self.task("A")
        self.assertIsNone(task["lease_owner"])
        self.assertIsNone(task["lease_expires_at"])

    def test_retry_clears_the_last_error(self):
        self.write([_task("A", status="failed", last_error={"message": "boom"})])

        retry(self.path, "A")

        self.assertIsNone(self.task("A")["last_error"])

    def test_rewinding_a_stage_clears_the_digests_that_would_skip_the_work(self):
        self.write([_task("A", status="halted", stage="verified",
                          impl_digest="abc", verified_digest="abc")])

        retry(self.path, "A", from_stage="provisioned")

        task = self.task("A")
        self.assertEqual(task["stage"], "provisioned")
        self.assertIsNone(task["impl_digest"])
        self.assertIsNone(task["verified_digest"])

    def test_an_unknown_stage_is_refused_with_the_valid_ones_listed(self):
        self.write([_task("A", status="halted")])

        with self.assertRaises(ControlError) as ctx:
            retry(self.path, "A", from_stage="banana")

        self.assertIn("implemented", str(ctx.exception))

    def test_a_done_task_cannot_be_retried_because_retry_does_not_undo(self):
        self.write([_task("A", status="done")])

        with self.assertRaises(ControlError) as ctx:
            retry(self.path, "A")

        self.assertIn("reset", str(ctx.exception))

    def test_retry_does_not_run_anything(self):
        # retry only makes a task runnable; the tick is the single execution
        # path. Any git call here would mean a second one had appeared.
        self.write([_task("A", status="halted")])

        with patch("graphs.coding.utils.control.git_ops") as mock_git:
            retry(self.path, "A")

        self.assertEqual(mock_git.mock_calls, [])


class TestReset(ControlTestCase):
    def setUp(self):
        super().setUp()
        self.close_pr = patch(
            "graphs.coding.utils.control.git_ops.close_pull_request",
            new=AsyncMock(return_value=(True, "closed"))
        ).start()
        self.delete_branch = patch(
            "graphs.coding.utils.control.git_ops.delete_branch",
            new=AsyncMock(return_value=(True, "deleted"))
        ).start()
        self.teardown = patch(
            "graphs.coding.utils.control.git_ops.teardown_worktree",
            new=AsyncMock(return_value=(True, "gone"))
        ).start()
        self.addCleanup(patch.stopall)

    async def test_a_dry_run_touches_nothing(self):
        self.write([_task("A", status="awaiting_review", pr_url="https://x/pull/3",
                          branch_name="feat/a", run_id="run_1")])

        message = await reset(self.path, "A", dry_run=True)

        self.close_pr.assert_not_called()
        self.delete_branch.assert_not_called()
        self.teardown.assert_not_called()
        self.assertEqual(self.task("A")["status"], "awaiting_review")
        self.assertIn("would", message.lower())

    async def test_a_dry_run_names_everything_it_would_destroy(self):
        self.write([_task("A", pr_url="https://x/pull/3", branch_name="feat/a",
                          run_id="run_1")])

        message = await reset(self.path, "A", dry_run=True)

        self.assertIn("https://x/pull/3", message)
        self.assertIn("feat/a", message)
        self.assertIn("run_1", message)

    async def test_reset_clears_every_field_it_promises_to_clear(self):
        self.write([_task(
            "A", status="awaiting_review", stage="published",
            attempts={"implement": 2}, impl_digest="d1", verified_digest="d1",
            head_sha="abc123", review_cursor="cursor", pr_url="https://x/pull/3",
            pr_number=3, commit_url="https://x/commit/1", poll_until=time.time() + 60,
            last_error={"message": "x"}, lease_owner="tick_1",
            lease_expires_at=time.time() + 60, branch_name="feat/a", run_id="run_1"
        )])

        await reset(self.path, "A")

        task = self.task("A")
        for field, expected in RESET_FIELDS.items():
            self.assertEqual(task.get(field), expected, f"{field} was not reset")
        self.assertIsNone(task.get("run_id"))
        self.assertIsNone(task.get("branch_name"))

    async def test_reset_closes_the_pr_deletes_the_branch_and_removes_the_worktree(self):
        self.write([_task("A", pr_url="https://x/pull/3", branch_name="feat/a",
                          run_id="run_1")])

        await reset(self.path, "A")

        self.close_pr.assert_awaited_once()
        self.delete_branch.assert_awaited_once()
        self.teardown.assert_awaited_once()
        self.assertEqual(self.delete_branch.await_args.kwargs["branch_name"], "feat/a")

    async def test_a_task_with_no_pr_does_not_call_github(self):
        self.write([_task("A", branch_name="feat/a", run_id="run_1")])

        await reset(self.path, "A")

        self.close_pr.assert_not_called()

    async def test_the_manifest_is_cleared_even_when_the_cleanup_fails(self):
        # A task still pointing at a PR that a half-failed cleanup left open is
        # worse than one whose branch survived: the next tick would resume it.
        self.close_pr.return_value = (False, "network down")
        self.write([_task("A", status="awaiting_review", pr_url="https://x/pull/3",
                          branch_name="feat/a", run_id="run_1")])

        message = await reset(self.path, "A")

        self.assertEqual(self.task("A")["status"], "queued")
        self.assertIsNone(self.task("A")["pr_url"])
        self.assertIn("could not close", message)

    async def test_cleanup_paths_are_anchored_to_the_repo_not_the_cwd(self):
        # A reset run from anywhere else must still clean the worktree the
        # scheduler actually created.
        self.write([_task("A", branch_name="feat/a", run_id="run_1")])

        await reset(self.path, "A")

        workspace = self.teardown.await_args.args[1]
        self.assertTrue(os.path.isabs(workspace))
        self.assertTrue(workspace.endswith(os.path.join("workspaces", "runs", "run_1")))

    async def test_an_unknown_task_is_refused(self):
        self.write([_task("A")])

        with self.assertRaises(ControlError):
            await reset(self.path, "NOPE")


class TestSkipAndUnblock(ControlTestCase):
    def test_skip_blocks_the_task_without_failing_it(self):
        self.write([_task("A")])

        skip(self.path, "A")

        self.assertEqual(self.task("A")["status"], "blocked")

    def test_skip_warns_about_the_dependents_it_strands(self):
        self.write([_task("A"), _task("B", dependencies=["A"])])

        message = skip(self.path, "A")

        self.assertIn("B", message)

    def test_skip_releases_the_lease(self):
        self.write([_task("A", status="active", lease_owner="tick_1",
                          lease_expires_at=time.time() + 60)])

        skip(self.path, "A")

        self.assertIsNone(self.task("A")["lease_owner"])

    def test_a_done_task_cannot_be_skipped(self):
        self.write([_task("A", status="done")])

        with self.assertRaises(ControlError):
            skip(self.path, "A")

    def test_unblock_returns_a_skipped_task_to_the_queue(self):
        self.write([_task("A", status="blocked", stage="implemented")])

        unblock(self.path, "A")

        task = self.task("A")
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["stage"], "implemented")

    def test_unblock_refuses_a_task_that_is_not_blocked(self):
        self.write([_task("A", status="failed")])

        with self.assertRaises(ControlError) as ctx:
            unblock(self.path, "A")

        self.assertIn("failed", str(ctx.exception))


class TestAbort(ControlTestCase):
    def test_abort_fails_the_task_and_records_the_reason(self):
        self.write([_task("A", status="active")])

        abort(self.path, "A", reason="wrong spec")

        task = self.task("A")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["last_error"]["message"], "wrong spec")
        self.assertEqual(task["last_error"]["kind"], "operator")

    def test_abort_leaves_the_evidence_in_place(self):
        # The difference between abort and reset is exactly this.
        self.write([_task("A", status="active", pr_url="https://x/pull/3",
                          branch_name="feat/a", run_id="run_1")])

        abort(self.path, "A")

        task = self.task("A")
        self.assertEqual(task["pr_url"], "https://x/pull/3")
        self.assertEqual(task["branch_name"], "feat/a")

    def test_abort_releases_the_lease_so_the_task_is_not_invisible(self):
        self.write([_task("A", status="active", lease_owner="tick_1",
                          lease_expires_at=time.time() + 900)])

        abort(self.path, "A")

        self.assertIsNone(self.task("A")["lease_owner"])

    def test_abort_without_a_reason_still_records_one(self):
        self.write([_task("A", status="active")])

        abort(self.path, "A")

        self.assertTrue(self.task("A")["last_error"]["message"])


if __name__ == "__main__":
    unittest.main()
