"""Reading a human's verdict off a pull request.

An approval counts only when a known reviewer gives it, on the first line, and
outside a code block. The gate once defaulted to approved when it found nothing
and merged unreviewed work, so silence must read as pending here forever after.
Anything that is not an approval is feedback, and feedback — inline or not —
sends the task back to the worker instead of merging it.
"""
import time
import unittest
from unittest.mock import AsyncMock, patch

from graphs.coding.nodes.sync_review import (
    evaluate_signals,
    first_line_command,
    harvest_comments,
    sync_review_node,
)
from tests.graphs.coding.fixtures import ManifestFixture, _task


class TestFirstLineCommand(unittest.TestCase):
    def test_exact_first_line_commands_are_recognised(self):
        self.assertEqual(first_line_command("/approve"), "/approve")
        self.assertEqual(first_line_command("  /APPROVE  \nthanks!"), "/approve")
        self.assertEqual(first_line_command("/reject\nsee below"), "/reject")
        self.assertEqual(first_line_command("/abort"), "/abort")

    def test_prose_mentioning_a_command_is_not_a_command(self):
        self.assertEqual(first_line_command("I would /approve this but..."), "")
        self.assertEqual(first_line_command("looks ok to me"), "")
        self.assertEqual(first_line_command("> /approve"), "")
        self.assertEqual(first_line_command("```\n/approve\n```"), "")
        self.assertEqual(first_line_command("Please run:\n/approve"), "")
        self.assertEqual(first_line_command(""), "")


class TestEvaluateSignals(unittest.TestCase):
    reviewers = ["alice"]

    def test_merged_is_terminal_and_checked_first(self):
        status = {"state": "MERGED", "reviewDecision": "CHANGES_REQUESTED"}
        decision, _ = evaluate_signals(status, self.reviewers)
        self.assertEqual(decision, "merged")

    def test_a_closed_pr_aborts(self):
        decision, _ = evaluate_signals({"state": "CLOSED"}, self.reviewers)
        self.assertEqual(decision, "abort")

    def test_native_review_approval(self):
        status = {
            "state": "OPEN",
            "reviewDecision": "APPROVED",
            "latestReviews": [{"author": {"login": "alice"}, "state": "APPROVED"}],
        }
        decision, _ = evaluate_signals(status, self.reviewers)
        self.assertEqual(decision, "approved")

    def test_review_approval_by_a_stranger_is_ignored(self):
        status = {
            "state": "OPEN",
            "reviewDecision": "APPROVED",
            "latestReviews": [{"author": {"login": "mallory"}, "state": "APPROVED"}],
        }
        decision, _ = evaluate_signals(status, self.reviewers)
        self.assertEqual(decision, "pending")

    def test_the_approved_label(self):
        status = {"state": "OPEN", "labels": [{"name": "approved"}]}
        decision, _ = evaluate_signals(status, self.reviewers)
        self.assertEqual(decision, "approved")

    def test_an_unrelated_label_is_not_an_approval(self):
        status = {"state": "OPEN", "labels": [{"name": "approved-by-nobody"}]}
        decision, _ = evaluate_signals(status, self.reviewers)
        self.assertEqual(decision, "pending")

    def test_slash_approve_comment_from_a_reviewer(self):
        status = {"state": "OPEN", "comments": [{"author": {"login": "alice"}, "body": "/approve"}]}
        decision, evidence = evaluate_signals(status, self.reviewers)
        self.assertEqual(decision, "approved")
        self.assertIn("alice", evidence)

    def test_slash_approve_from_a_stranger_is_ignored(self):
        status = {"state": "OPEN", "comments": [{"author": {"login": "mallory"}, "body": "/approve"}]}
        decision, _ = evaluate_signals(status, self.reviewers)
        self.assertEqual(decision, "pending")

    def test_approve_inside_prose_is_not_an_approval(self):
        """The direct fix for a gate that read 'ok' inside 'looks broken' (E1)."""
        status = {
            "state": "OPEN",
            "comments": [{"author": {"login": "alice"}, "body": "I'd /approve once tests pass"}],
        }
        decision, _ = evaluate_signals(status, self.reviewers)
        self.assertEqual(decision, "pending")

    def test_approve_inside_a_code_block_is_not_an_approval(self):
        status = {
            "state": "OPEN",
            "comments": [{"author": {"login": "alice"}, "body": "```\n/approve\n```"}],
        }
        decision, _ = evaluate_signals(status, self.reviewers)
        self.assertEqual(decision, "pending")

    def test_silence_is_pending_not_approval(self):
        """The old gate defaulted to approved when it found nothing (E2)."""
        decision, _ = evaluate_signals({"state": "OPEN"}, self.reviewers)
        self.assertEqual(decision, "pending")

    def test_changes_requested(self):
        status = {"state": "OPEN", "reviewDecision": "CHANGES_REQUESTED"}
        decision, _ = evaluate_signals(status, self.reviewers)
        self.assertEqual(decision, "changes_requested")

    def test_changes_requested_beats_a_later_label(self):
        status = {
            "state": "OPEN",
            "reviewDecision": "CHANGES_REQUESTED",
            "labels": [{"name": "approved"}],
        }
        decision, _ = evaluate_signals(status, self.reviewers)
        self.assertEqual(decision, "changes_requested")

    def test_slash_reject_and_slash_abort(self):
        reject = {"state": "OPEN", "comments": [{"author": {"login": "alice"}, "body": "/reject"}]}
        abort = {"state": "OPEN", "comments": [{"author": {"login": "alice"}, "body": "/abort"}]}
        self.assertEqual(evaluate_signals(reject, self.reviewers)[0], "changes_requested")
        self.assertEqual(evaluate_signals(abort, self.reviewers)[0], "abort")

    def test_a_disabled_signal_is_not_honoured(self):
        status = {"state": "OPEN", "labels": [{"name": "approved"}]}
        decision, _ = evaluate_signals(
            status, self.reviewers, approval_signals=["review_approved"]
        )
        self.assertEqual(decision, "pending")


class TestHarvestComments(unittest.TestCase):
    def test_the_bots_own_comments_are_ignored(self):
        """Otherwise the system reacts to its own test-result comment forever."""
        status = {"comments": [
            {"author": {"login": "aoc-bot"}, "body": "### ✅ Verification passed", "databaseId": 5},
            {"author": {"login": "alice"}, "body": "rename this", "databaseId": 6},
        ]}
        bodies, cursor = harvest_comments(status, ["alice", "aoc-bot"], None, "aoc-bot")
        self.assertEqual(len(bodies), 1)
        self.assertIn("rename this", bodies[0])
        self.assertEqual(cursor, "6")

    def test_comments_at_or_below_the_cursor_are_not_reactioned(self):
        status = {"comments": [
            {"author": {"login": "alice"}, "body": "old", "databaseId": 3},
            {"author": {"login": "alice"}, "body": "new", "databaseId": 9},
        ]}
        bodies, cursor = harvest_comments(status, ["alice"], "3", None)
        self.assertEqual(len(bodies), 1)
        self.assertIn("new", bodies[0])
        self.assertEqual(cursor, "9")

    def test_non_reviewers_are_filtered_out(self):
        status = {"comments": [{"author": {"login": "drive-by"}, "body": "nit", "databaseId": 2}]}
        bodies, _ = harvest_comments(status, ["alice"], None, None)
        self.assertEqual(bodies, [])

    def test_slash_commands_are_not_treated_as_feedback(self):
        status = {"comments": [{"author": {"login": "alice"}, "body": "/approve", "databaseId": 2}]}
        bodies, _ = harvest_comments(status, ["alice"], None, None)
        self.assertEqual(bodies, [])

    def test_nothing_new_leaves_the_cursor_alone(self):
        bodies, cursor = harvest_comments({"comments": []}, ["alice"], "7", None)
        self.assertEqual(bodies, [])
        self.assertEqual(cursor, "7")


class TestSyncReviewNode(ManifestFixture):
    def setUp(self):
        super().setUp()
        patch("graphs.coding.nodes.sync_review.get_push_identity", return_value=None).start()
        self.addCleanup(patch.stopall)
        patch("graphs.coding.nodes.sync_review._teardown", AsyncMock()).start()
        # Inline threads are a separate API call; the tests below drive the
        # issue-comment path unless they say otherwise.
        self.threads = patch(
            "graphs.coding.nodes.sync_review.git_ops.get_unresolved_review_threads",
            AsyncMock(return_value=[])
        ).start()

    def review_state(self, task):
        return self.base_state(
            task,
            pr_url="https://github.com/org/repo/pull/7",
            reviewers=["alice"],
            lease_owner="tick_1",
        )

    async def test_approval_merges_and_marks_the_task_done(self):
        task = _task(status="awaiting_review", stage="awaiting_review",
                     pr_url="https://github.com/org/repo/pull/7")
        self.write_manifest([task])
        status = {"state": "OPEN", "labels": [{"name": "approved"}]}

        with patch("graphs.coding.nodes.sync_review.git_ops.get_pull_request_status",
                   AsyncMock(return_value=status)), \
             patch("graphs.coding.nodes.sync_review.git_ops.merge_pull_request",
                   AsyncMock(return_value=(True, "https://github.com/org/repo/commit/abc", "merged"))):
            result = await sync_review_node(self.review_state(task))

        self.assertEqual(result["stage"], "done")
        self.assertEqual(self.stored()["status"], "done")

    async def test_a_failed_merge_does_not_mark_the_task_done(self):
        task = _task(status="awaiting_review", stage="awaiting_review",
                     pr_url="https://github.com/org/repo/pull/7")
        self.write_manifest([task])
        status = {"state": "OPEN", "labels": [{"name": "approved"}]}

        with patch("graphs.coding.nodes.sync_review.git_ops.get_pull_request_status",
                   AsyncMock(return_value=status)), \
             patch("graphs.coding.nodes.sync_review.git_ops.merge_pull_request",
                   AsyncMock(return_value=(False, "", "merge conflict"))):
            result = await sync_review_node(self.review_state(task))

        self.assertNotEqual(self.stored()["status"], "done")
        self.assertIn("merge failed", result["error_message"])
        # A shorter window, because this is expected to be fixed soon.
        self.assertGreater(self.stored()["poll_until"], time.time())

    async def test_an_already_merged_pr_needs_no_merge_call(self):
        task = _task(status="awaiting_review", stage="awaiting_review",
                     pr_url="https://github.com/org/repo/pull/7")
        self.write_manifest([task])
        status = {"state": "MERGED", "mergeCommit": {"oid": "abc123"}}

        with patch("graphs.coding.nodes.sync_review.git_ops.get_pull_request_status",
                   AsyncMock(return_value=status)), \
             patch("graphs.coding.nodes.sync_review.git_ops.merge_pull_request",
                   AsyncMock()) as mock_merge:
            result = await sync_review_node(self.review_state(task))

        mock_merge.assert_not_called()
        self.assertEqual(result["commit_url"], "https://github.com/org/repo/commit/abc123")
        self.assertEqual(self.stored()["status"], "done")

    async def test_review_comments_send_the_task_back_to_the_worker(self):
        task = _task(status="awaiting_review", stage="awaiting_review",
                     pr_url="https://github.com/org/repo/pull/7")
        self.write_manifest([task])
        status = {
            "state": "OPEN",
            "reviewDecision": "CHANGES_REQUESTED",
            "comments": [{"author": {"login": "alice"}, "body": "rename it", "databaseId": 4}],
        }

        with patch("graphs.coding.nodes.sync_review.git_ops.get_pull_request_status",
                   AsyncMock(return_value=status)):
            result = await sync_review_node(self.review_state(task))

        self.assertEqual(result["route"], "implement")
        self.assertEqual(result["github_pr_comments"], ["@alice: rename it"])
        stored = self.stored()
        self.assertEqual(stored["stage"], "provisioned")
        self.assertEqual(stored["review_cursor"], "4")
        self.assertIsNone(stored["impl_digest"])

    async def test_an_inline_only_review_is_still_acted_on(self):
        """Comments on the line itself are not in `comments`; they used to be missed."""
        task = _task(status="awaiting_review", stage="awaiting_review",
                     pr_url="https://github.com/org/repo/pull/7", pr_number=7)
        self.write_manifest([task])
        self.threads.return_value = [
            {"author": {"login": "alice"}, "body": "[app.py:12] extract this", "databaseId": 8}
        ]

        with patch("graphs.coding.nodes.sync_review.git_ops.get_pull_request_status",
                   AsyncMock(return_value={"state": "OPEN", "comments": []})):
            result = await sync_review_node(self.review_state(task))

        self.assertEqual(result["route"], "implement")
        self.assertEqual(result["github_pr_comments"], ["@alice: [app.py:12] extract this"])
        self.assertEqual(self.stored()["review_cursor"], "8")

    async def test_a_pending_review_releases_the_lease_and_does_nothing_else(self):
        task = _task(status="awaiting_review", stage="awaiting_review",
                     pr_url="https://github.com/org/repo/pull/7",
                     lease_owner="tick_1", lease_expires_at=time.time() + 600)
        self.write_manifest([task])

        with patch("graphs.coding.nodes.sync_review.git_ops.get_pull_request_status",
                   AsyncMock(return_value={"state": "OPEN"})):
            result = await sync_review_node(self.review_state(task))

        self.assertEqual(result["route"], "scheduler")
        self.assertEqual(result["tick_report"], [])
        self.assertIsNone(self.stored()["lease_owner"])

    async def test_no_pr_yet_falls_through_to_publish(self):
        task = _task(status="awaiting_review", stage="awaiting_review")
        self.write_manifest([task])

        state = self.base_state(task, reviewers=["alice"])
        state["pr_url"] = ""
        result = await sync_review_node(state)

        self.assertEqual(result["route"], "publish")


if __name__ == "__main__":
    unittest.main()
