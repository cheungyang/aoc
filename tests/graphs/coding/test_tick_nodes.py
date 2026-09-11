"""Phase 2 — the tick reconciler.

These tests pin the behaviours that make a tick safe to run every five minutes:
work is never redone, a failure of the wrong kind never spends an LLM attempt,
and an approval is only an approval when GitHub says so, exactly.
"""
import json
import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from graphs.coding.nodes.audit import audit_node, resolve_audit_mode
from graphs.coding.nodes.implement import MAX_IMPLEMENT_ATTEMPTS, implement_node
from graphs.coding.nodes.publish import MAX_PUBLISH_ATTEMPTS, publish_node
from graphs.coding.nodes.scheduler import (
    ROUTE_DONE,
    ROUTE_IMPLEMENT,
    ROUTE_PUBLISH,
    ROUTE_SYNC,
    scheduler_node,
    select_task,
)
from graphs.coding.nodes.sync_review import (
    evaluate_signals,
    first_line_command,
    harvest_comments,
    sync_review_node,
)
from graphs.coding.nodes.verify import verify_node
from graphs.coding.utils import manifest as manifest_store


def _task(**overrides):
    task = {
        "task_id": "T1",
        "status": "pending",
        "stage": "queued",
        "dependencies": [],
        "verification_command": "pytest -q",
        "attempts": {},
    }
    task.update(overrides)
    return task


class ManifestFixture(unittest.IsolatedAsyncioTestCase):
    """Every node writes through the manifest, so each test gets its own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manifest_path = os.path.join(self.tmp.name, "build_request.json")
        self.workspace = os.path.join(self.tmp.name, "ws")
        os.makedirs(self.workspace, exist_ok=True)

    def write_manifest(self, tasks):
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump({"version": "3.0", "project_name": "demo", "queue": tasks}, f)

    def stored(self, task_id="T1"):
        return manifest_store.find_task(
            manifest_store.load_manifest(self.manifest_path), task_id
        )

    def base_state(self, task, **overrides):
        state = {
            "build_request_path": self.manifest_path,
            "current_task": task,
            "workspace_path": self.workspace,
            "branch_name": "feat/demo/t1",
            "project_name": "demo",
            "tick_report": [],
            "repo": {"slug": "org/repo", "default_branch": "main"},
        }
        state.update(overrides)
        return state


class TestSelectTask(unittest.TestCase):
    def test_reviews_are_picked_before_new_work(self):
        """A merge can unblock a dependent inside the same tick, so sync first."""
        queue = [_task(task_id="NEW"), _task(task_id="REV", status="awaiting_review")]
        task, route = select_task(queue, handled=[], now=100.0)
        self.assertEqual(task["task_id"], "REV")
        self.assertEqual(route, ROUTE_SYNC)

    def test_review_outside_its_poll_window_is_dormant(self):
        queue = [_task(task_id="REV", status="awaiting_review", poll_until=50.0)]
        task, route = select_task(queue, handled=[], now=100.0)
        self.assertIsNone(task)
        self.assertEqual(route, ROUTE_DONE)

    def test_review_inside_its_poll_window_is_synced(self):
        queue = [_task(task_id="REV", status="awaiting_review", poll_until=500.0)]
        task, route = select_task(queue, handled=[], now=100.0)
        self.assertEqual(task["task_id"], "REV")
        self.assertEqual(route, ROUTE_SYNC)

    def test_leased_review_is_left_to_its_owner(self):
        queue = [_task(task_id="REV", status="awaiting_review",
                       lease_owner="other", lease_expires_at=999.0)]
        task, _ = select_task(queue, handled=[], now=100.0)
        self.assertIsNone(task)

    def test_already_handled_task_is_not_picked_twice(self):
        queue = [_task(task_id="T1")]
        task, route = select_task(queue, handled=["T1"], now=100.0)
        self.assertIsNone(task)
        self.assertEqual(route, ROUTE_DONE)

    def test_verified_task_resumes_at_publish_not_at_the_llm(self):
        """The whole point of the stage machine: a crash after verify costs no tokens."""
        queue = [_task(task_id="T1", stage="verified")]
        task, route = select_task(queue, handled=[], now=100.0)
        self.assertEqual(route, ROUTE_PUBLISH)

    def test_published_task_resumes_at_sync(self):
        queue = [_task(task_id="T1", stage="published")]
        _, route = select_task(queue, handled=[], now=100.0)
        self.assertEqual(route, ROUTE_SYNC)

    def test_fresh_task_starts_at_implement(self):
        queue = [_task(task_id="T1", stage="queued")]
        _, route = select_task(queue, handled=[], now=100.0)
        self.assertEqual(route, ROUTE_IMPLEMENT)


class TestImplementNode(ManifestFixture):
    async def test_unchanged_worktree_skips_the_llm(self):
        task = _task(stage="implemented", impl_digest="abc")
        self.write_manifest([task])

        with patch("graphs.coding.nodes.implement.digest_matches", AsyncMock(return_value=True)), \
             patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock()
            result = await implement_node(self.base_state(task))

        mock_agent.ainvoke.assert_not_called()
        self.assertEqual(result["stage"], "implemented")

    async def test_stale_digest_reruns_the_llm(self):
        task = _task(stage="implemented", impl_digest="abc")
        self.write_manifest([task])

        with patch("graphs.coding.nodes.implement.digest_matches", AsyncMock(return_value=False)), \
             patch("graphs.coding.nodes.implement._modified_files", AsyncMock(return_value=["a.py"])), \
             patch("graphs.coding.nodes.implement.compute_worktree_digest", AsyncMock(return_value="def")), \
             patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock(return_value="<worker_handoff></worker_handoff>")
            result = await implement_node(self.base_state(task))

        mock_agent.ainvoke.assert_awaited()
        self.assertEqual(result["impl_digest"], "def")
        self.assertEqual(self.stored()["impl_digest"], "def")

    async def test_exhausted_budget_halts_instead_of_looping(self):
        task = _task(attempts={"implement": MAX_IMPLEMENT_ATTEMPTS})
        self.write_manifest([task])

        with patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock()
            result = await implement_node(self.base_state(task))

        mock_agent.ainvoke.assert_not_called()
        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["status"], "halted")
        self.assertIsNone(self.stored()["lease_owner"])

    async def test_worker_that_changed_nothing_does_not_advance_the_stage(self):
        task = _task()
        self.write_manifest([task])

        with patch("graphs.coding.nodes.implement._modified_files", AsyncMock(return_value=[])), \
             patch("graphs.coding.nodes.implement.compute_worktree_digest", AsyncMock(return_value=None)), \
             patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock(return_value="<worker_handoff></worker_handoff>")
            result = await implement_node(self.base_state(task))

        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["stage"], "queued")
        self.assertEqual(self.stored()["attempts"]["implement"], 1)

    async def test_git_is_the_source_of_truth_for_modified_files(self):
        """The model's own file list is ignored; only git status counts (§7.6)."""
        task = _task()
        self.write_manifest([task])
        xml = ("<worker_handoff><modified_files><file>imaginary.py</file></modified_files>"
               "<implementation_summary>done</implementation_summary></worker_handoff>")

        with patch("graphs.coding.nodes.implement._modified_files", AsyncMock(return_value=["real.py"])), \
             patch("graphs.coding.nodes.implement.compute_worktree_digest", AsyncMock(return_value="d")), \
             patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock(return_value=xml)
            result = await implement_node(self.base_state(task))

        self.assertEqual(result["modified_files"], ["real.py"])


class TestVerifyNode(ManifestFixture):
    async def test_same_tree_is_not_retested(self):
        task = _task(stage="verified", verified_digest="d1")
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock()) as mock_run:
            result = await verify_node(self.base_state(task, impl_digest="d1"))

        mock_run.assert_not_called()
        self.assertTrue(result["test_run_passed"])

    async def test_missing_verification_command_is_a_config_halt(self):
        """No amount of LLM retries can add a command to the manifest (B4)."""
        task = _task(verification_command="")
        self.write_manifest([task])

        result = await verify_node(self.base_state(task, impl_digest="d1"))

        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["status"], "halted")
        self.assertEqual(self.stored()["last_error"]["kind"], "config")

    async def test_passing_tests_record_the_digest_they_passed_on(self):
        task = _task()
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock(return_value=(0, "ok", ""))):
            result = await verify_node(self.base_state(task, impl_digest="d1"))

        self.assertTrue(result["test_run_passed"])
        self.assertEqual(self.stored()["verified_digest"], "d1")
        self.assertEqual(self.stored()["stage"], "verified")

    async def test_failing_tests_go_back_to_implement_within_budget(self):
        task = _task(attempts={"implement": 1})
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock(return_value=(1, "", "boom"))):
            result = await verify_node(self.base_state(task, impl_digest="d1"))

        self.assertEqual(result["route"], "implement")
        self.assertIn("boom", result["test_stderr"])
        # The digest is cleared so implement cannot skip on the stale tree.
        self.assertIsNone(self.stored()["impl_digest"])

    async def test_failing_tests_halt_once_the_budget_is_gone(self):
        task = _task(attempts={"implement": MAX_IMPLEMENT_ATTEMPTS})
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock(return_value=(1, "", "boom"))):
            result = await verify_node(self.base_state(task, impl_digest="d1"))

        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["status"], "halted")

    async def test_verify_does_not_require_a_pr(self):
        """Local tests must not be gated on a network operation (A3)."""
        task = _task()
        self.write_manifest([task])

        with patch("graphs.coding.nodes.verify._run", AsyncMock(return_value=(0, "ok", ""))):
            result = await verify_node(self.base_state(task, impl_digest="d1", pr_url=""))

        self.assertTrue(result["test_run_passed"])


class TestAuditNode(ManifestFixture):
    def test_unknown_mode_falls_back_to_advisory(self):
        self.assertEqual(resolve_audit_mode({"audit_mode": "nonsense"}), "advisory")
        self.assertEqual(resolve_audit_mode({}), "advisory")
        self.assertEqual(resolve_audit_mode({"audit_mode": "BLOCKING"}), "blocking")

    async def test_off_skips_the_call_entirely(self):
        task = _task(stage="verified")
        self.write_manifest([task])

        with patch("graphs.coding.nodes.audit._run_audit", AsyncMock()) as mock_audit:
            result = await audit_node(self.base_state(task, audit_mode="off"))

        mock_audit.assert_not_called()
        self.assertEqual(result["stage"], "audited")

    async def test_advisory_rejection_still_publishes(self):
        task = _task(stage="verified")
        self.write_manifest([task])
        verdict = {"passed": False, "feedback": "magic numbers"}

        with patch("graphs.coding.nodes.audit.git_ops.get_git_diff", AsyncMock(return_value="diff")), \
             patch("graphs.coding.nodes.audit._run_audit", AsyncMock(return_value=verdict)):
            result = await audit_node(self.base_state(task, audit_mode="advisory"))

        self.assertNotEqual(result.get("route"), "implement")
        self.assertEqual(result["audit_feedback"], "magic numbers")

    async def test_blocking_rejection_returns_to_implement(self):
        task = _task(stage="verified")
        self.write_manifest([task])
        verdict = {"passed": False, "feedback": "fake implementation"}

        with patch("graphs.coding.nodes.audit.git_ops.get_git_diff", AsyncMock(return_value="diff")), \
             patch("graphs.coding.nodes.audit._run_audit", AsyncMock(return_value=verdict)):
            result = await audit_node(self.base_state(task, audit_mode="blocking"))

        self.assertEqual(result["route"], "implement")
        self.assertEqual(self.stored()["stage"], "provisioned")

    async def test_an_llm_outage_is_not_a_rejection(self):
        from graphs.coding.nodes.audit import _run_audit

        with patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock(side_effect=RuntimeError("model down"))
            verdict = await _run_audit(spec_content="spec", diff="d", channel="c")

        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["feedback"], "")


class TestPublishNode(ManifestFixture):
    def setUp(self):
        super().setUp()
        self.identity = patch("graphs.coding.nodes.publish.get_push_identity", return_value=None)
        self.identity.start()
        self.addCleanup(self.identity.stop)
        self.head = patch("graphs.coding.nodes.publish._head_sha", AsyncMock(return_value="sha1"))
        self.head.start()
        self.addCleanup(self.head.stop)
        self.context = patch("graphs.coding.nodes.publish._post_review_context", AsyncMock())
        self.context.start()
        self.addCleanup(self.context.stop)

    async def test_happy_path_opens_the_poll_window(self):
        task = _task(stage="audited")
        self.write_manifest([task])

        with patch("graphs.coding.nodes.publish.git_ops.commit_and_push",
                   AsyncMock(return_value=(True, "pushed"))), \
             patch("graphs.coding.nodes.publish._find_open_pr", AsyncMock(return_value=("", None))), \
             patch("graphs.coding.nodes.publish.git_ops.create_pull_request",
                   AsyncMock(return_value=(True, "https://github.com/org/repo/pull/7", 7))):
            result = await publish_node(self.base_state(task))

        stored = self.stored()
        self.assertEqual(result["pr_url"], "https://github.com/org/repo/pull/7")
        self.assertEqual(stored["status"], "awaiting_review")
        self.assertGreater(stored["poll_until"], time.time())
        # The lease is released so a later tick may sync the review.
        self.assertIsNone(stored["lease_owner"])

    async def test_an_existing_pr_for_the_branch_is_adopted_not_duplicated(self):
        task = _task(stage="audited")
        self.write_manifest([task])

        with patch("graphs.coding.nodes.publish.git_ops.commit_and_push",
                   AsyncMock(return_value=(True, "pushed"))), \
             patch("graphs.coding.nodes.publish._find_open_pr",
                   AsyncMock(return_value=("https://github.com/org/repo/pull/4", 4))), \
             patch("graphs.coding.nodes.publish.git_ops.create_pull_request",
                   AsyncMock()) as mock_create:
            result = await publish_node(self.base_state(task))

        mock_create.assert_not_called()
        self.assertEqual(result["pr_url"], "https://github.com/org/repo/pull/4")

    async def test_a_failed_pr_after_a_good_push_reports_a_compare_url(self):
        """Never throw away a successful push; hand back a link that works (§6.3)."""
        task = _task(stage="audited")
        self.write_manifest([task])

        with patch("graphs.coding.nodes.publish.git_ops.commit_and_push",
                   AsyncMock(return_value=(True, "pushed"))), \
             patch("graphs.coding.nodes.publish._find_open_pr", AsyncMock(return_value=("", None))), \
             patch("graphs.coding.nodes.publish.git_ops.create_pull_request",
                   AsyncMock(return_value=(False, "gh: rate limited", None))):
            result = await publish_node(self.base_state(task))

        joined = "\n".join(result["tick_report"])
        self.assertIn("https://github.com/org/repo/compare/feat/demo/t1?expand=1", joined)
        self.assertEqual(self.stored()["head_sha"], "sha1")

    async def test_a_transient_failure_retries_on_the_next_tick(self):
        task = _task(stage="audited")
        self.write_manifest([task])

        with patch("graphs.coding.nodes.publish.git_ops.commit_and_push",
                   AsyncMock(return_value=(False, "network down"))):
            result = await publish_node(self.base_state(task))

        stored = self.stored()
        self.assertEqual(result["route"], "done")
        self.assertNotEqual(stored["status"], "halted")
        self.assertEqual(stored["attempts"]["publish"], 1)
        # Handed back, or the next tick could not see it for another 30 minutes.
        self.assertIsNone(stored["lease_owner"])
        self.assertEqual(stored["status"], "queued")
        # The stage is preserved, so the retry re-enters at publish.
        self.assertEqual(stored["stage"], "audited")

    async def test_publish_failures_never_consume_the_implement_budget(self):
        task = _task(stage="audited", attempts={"implement": 2})
        self.write_manifest([task])

        with patch("graphs.coding.nodes.publish.git_ops.commit_and_push",
                   AsyncMock(return_value=(False, "network down"))):
            await publish_node(self.base_state(task))

        self.assertEqual(self.stored()["attempts"]["implement"], 2)
        self.assertEqual(self.stored()["attempts"]["publish"], 1)

    async def test_publish_halts_once_its_own_budget_is_gone(self):
        task = _task(stage="audited", attempts={"publish": MAX_PUBLISH_ATTEMPTS - 1})
        self.write_manifest([task])

        with patch("graphs.coding.nodes.publish.git_ops.commit_and_push",
                   AsyncMock(return_value=(False, "network down"))):
            await publish_node(self.base_state(task))

        self.assertEqual(self.stored()["status"], "halted")


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


class TestGraphTopology(unittest.TestCase):
    def test_v2_compiles_without_a_checkpointer(self):
        """Durable state lives in the manifest, git and GitHub — not a checkpoint."""
        from graphs.coding.graph import create_graph

        graph = create_graph(topology="v2")
        self.assertIsNone(graph.checkpointer)
        self.assertEqual(
            {"scheduler", "implement", "verify", "audit", "publish", "sync_review"},
            set(graph.nodes) - {"__start__"},
        )

    def test_v1_is_still_reachable_for_one_release(self):
        from langgraph.checkpoint.memory import MemorySaver
        from graphs.coding.graph import create_graph

        graph = create_graph(checkpointer=MemorySaver(), topology="v1")
        self.assertIn("hitl_gate", set(graph.nodes))


class TestSchedulerNode(ManifestFixture):
    def setUp(self):
        super().setUp()
        self.preflight = patch(
            "graphs.coding.nodes.scheduler.preflight_tick",
            AsyncMock(return_value=(True, "", None))
        )
        self.mock_preflight = self.preflight.start()
        self.addCleanup(self.preflight.stop)
        self.provision = patch(
            "graphs.coding.nodes.scheduler.git_ops.provision_worktree",
            AsyncMock(return_value=(True, "provisioned"))
        )
        self.mock_provision = self.provision.start()
        self.addCleanup(self.provision.stop)

    def tick_state(self, **overrides):
        state = {"build_request_path": self.manifest_path, "tick_report": [], "tick_handled": []}
        state.update(overrides)
        return state

    async def test_an_empty_queue_reports_nothing(self):
        """A quiet tick must produce no output at all, or the channel fills up."""
        self.write_manifest([])
        result = await scheduler_node(self.tick_state())

        self.assertEqual(result["route"], ROUTE_DONE)
        self.assertEqual(result["tick_report"], [])

    async def test_an_empty_queue_costs_no_network_call(self):
        self.write_manifest([_task(status="done", stage="done")])
        await scheduler_node(self.tick_state())
        self.mock_preflight.assert_not_called()

    async def test_a_dead_run_is_reclaimed(self):
        """The fix for 'stuck in_progress forever': the lease, not the status, decides."""
        self.write_manifest([_task(
            status="active", stage="provisioned",
            lease_owner="dead_tick", lease_expires_at=time.time() - 10
        )])

        result = await scheduler_node(self.tick_state())

        self.assertIn("Reclaimed", "\n".join(result["tick_report"]))
        self.assertEqual(result["route"], ROUTE_IMPLEMENT)

    async def test_a_failed_preflight_stops_before_any_token_is_spent(self):
        self.write_manifest([_task()])
        self.mock_preflight.return_value = (False, "Preflight failed: bot token expired", None)

        result = await scheduler_node(self.tick_state())

        self.assertEqual(result["route"], ROUTE_DONE)
        self.assertIn("bot token expired", result["error_message"])
        self.mock_provision.assert_not_called()

    async def test_claiming_a_task_writes_a_lease(self):
        self.write_manifest([_task()])
        result = await scheduler_node(self.tick_state(lease_owner="tick_abc"))

        stored = self.stored()
        self.assertEqual(result["route"], ROUTE_IMPLEMENT)
        self.assertEqual(stored["lease_owner"], "tick_abc")
        self.assertTrue(manifest_store.lease_is_active(stored))
        self.assertEqual(stored["status"], "active")

    async def test_a_task_claimed_elsewhere_is_skipped(self):
        self.write_manifest([_task(lease_owner="other", lease_expires_at=time.time() + 600)])
        # A live lease also hides the task from the runnable set, so the tick is a no-op.
        result = await scheduler_node(self.tick_state(lease_owner="tick_abc"))
        self.assertEqual(result["route"], ROUTE_DONE)

    async def test_a_review_is_not_reprovisioned(self):
        """Re-creating the worktree under a task awaiting review throws the code away."""
        self.write_manifest([_task(
            status="awaiting_review", stage="awaiting_review",
            pr_url="https://github.com/org/repo/pull/7"
        )])

        result = await scheduler_node(self.tick_state())

        self.assertEqual(result["route"], ROUTE_SYNC)
        self.mock_provision.assert_not_called()

    async def test_a_failed_provision_halts_with_the_git_error(self):
        self.write_manifest([_task()])
        self.mock_provision.return_value = (False, "fatal: invalid reference origin/main")

        result = await scheduler_node(self.tick_state())

        self.assertEqual(result["route"], ROUTE_DONE)
        self.assertEqual(self.stored()["status"], "halted")
        self.assertEqual(self.stored()["last_error"]["kind"], "git")
        self.assertIsNone(self.stored()["lease_owner"])

    async def test_a_verified_task_is_not_reprovisioned_into_a_clean_tree(self):
        task = _task(stage="verified", run_id="run_X", branch_name="feat/demo/t1")
        self.write_manifest([task])
        os.makedirs(os.path.join("workspaces", "runs", "run_X"), exist_ok=True)
        self.addCleanup(lambda: os.rmdir(os.path.join("workspaces", "runs", "run_X")))

        result = await scheduler_node(self.tick_state())

        self.assertEqual(result["route"], ROUTE_PUBLISH)
        self.mock_provision.assert_not_called()


class TestNodeSeparation(unittest.TestCase):
    """No node may both call the LLM and mutate a remote.

    Fusing them is what made a GitHub failure re-run the worker: the retry could
    not re-enter at the push, so it re-implemented from scratch. Keeping the two
    apart is what lets a tick resume at `publish` for free.
    """

    LLM_MARKERS = ("agent_call",)
    REMOTE_MARKERS = (
        "commit_and_push",
        "create_pull_request",
        "merge_pull_request",
        "comment_pull_request",
    )

    def test_no_v2_node_both_calls_the_llm_and_mutates_a_remote(self):
        import pathlib

        node_dir = pathlib.Path("graphs/coding/nodes")
        v2_nodes = ["scheduler.py", "implement.py", "verify.py",
                    "audit.py", "publish.py", "sync_review.py"]

        offenders = []
        for name in v2_nodes:
            source = (node_dir / name).read_text(encoding="utf-8")
            calls_llm = any(m in source for m in self.LLM_MARKERS)
            mutates = any(m in source for m in self.REMOTE_MARKERS)
            if calls_llm and mutates:
                offenders.append(name)

        self.assertEqual(offenders, [])


class TestTickEndToEnd(ManifestFixture):
    """Drives the compiled graph, so state that never crosses a node boundary fails here.

    Unit tests call nodes directly and see every key a node returns. LangGraph only
    carries keys the state schema declares as channels, so a route that is not
    declared silently becomes None and every conditional edge falls through to END.
    Only an end-to-end run can catch that.
    """

    def setUp(self):
        super().setUp()
        patch("graphs.coding.nodes.scheduler.preflight_tick",
              AsyncMock(return_value=(True, "", None))).start()
        # verify refuses to run against a workspace that is not there, so the
        # fake provision has to actually create one. Worktrees live under the
        # repo's workspaces/, so every one created here is removed again.
        self.created_worktrees = []

        async def fake_provision(repo_path, workspace_path, branch_name, base_ref):
            os.makedirs(workspace_path, exist_ok=True)
            self.created_worktrees.append(workspace_path)
            return True, "provisioned"

        self.addCleanup(self._remove_worktrees)
        patch("graphs.coding.nodes.scheduler.git_ops.provision_worktree",
              AsyncMock(side_effect=fake_provision)).start()


        patch("graphs.coding.nodes.implement._modified_files",
              AsyncMock(return_value=["app.py"])).start()
        patch("graphs.coding.nodes.implement.compute_worktree_digest",
              AsyncMock(return_value="digest_1")).start()
        patch("graphs.coding.nodes.implement.digest_matches",
              AsyncMock(return_value=True)).start()
        patch("graphs.coding.nodes.verify._run",
              AsyncMock(return_value=(0, "1 passed", ""))).start()
        patch("graphs.coding.nodes.audit._run_audit",
              AsyncMock(return_value={"passed": True, "feedback": ""})).start()
        patch("graphs.coding.nodes.audit.git_ops.get_git_diff",
              AsyncMock(return_value="diff")).start()
        patch("graphs.coding.nodes.publish.get_push_identity", return_value=None).start()
        patch("graphs.coding.nodes.publish._head_sha", AsyncMock(return_value="sha1")).start()
        patch("graphs.coding.nodes.publish._post_review_context", AsyncMock()).start()
        patch("graphs.coding.nodes.publish._find_open_pr",
              AsyncMock(return_value=("", None))).start()
        self.addCleanup(patch.stopall)

        self.agent = patch("tools.agent_call.agent_call").start()
        self.agent.ainvoke = AsyncMock(
            return_value="<worker_handoff><implementation_summary>done</implementation_summary></worker_handoff>"
        )

    def _remove_worktrees(self):
        import shutil
        for path in self.created_worktrees:
            shutil.rmtree(path, ignore_errors=True)

    def inputs(self):
        from graphs.coding.adapters import prepare_input
        return prepare_input(query="tick", build_request_path=self.manifest_path)

    async def run_tick(self):
        from graphs.coding.graph import create_graph
        return await create_graph(topology="v2").ainvoke(self.inputs())

    async def test_a_full_tick_walks_implement_to_awaiting_review(self):
        self.write_manifest([_task(stage="queued")])

        with patch("graphs.coding.nodes.publish.git_ops.commit_and_push",
                   AsyncMock(return_value=(True, "pushed"))), \
             patch("graphs.coding.nodes.publish.git_ops.create_pull_request",
                   AsyncMock(return_value=(True, "https://github.com/org/repo/pull/7", 7))):
            state = await self.run_tick()

        stored = self.stored()
        self.assertEqual(stored["status"], "awaiting_review")
        self.assertEqual(stored["pr_url"], "https://github.com/org/repo/pull/7")
        # The report survived every node boundary.
        report = "\n".join(state["tick_report"])
        self.assertIn("implemented", report)
        self.assertIn("passed", report)
        self.assertIn("ready for review", report)

    async def test_a_github_outage_costs_exactly_one_llm_call_across_two_ticks(self):
        """The headline claim of the split: a push failure must not re-run the worker."""
        self.write_manifest([_task(stage="queued")])

        with patch("graphs.coding.nodes.publish.git_ops.commit_and_push",
                   AsyncMock(return_value=(False, "network unreachable"))):
            await self.run_tick()
            self.assertEqual(self.agent.ainvoke.await_count, 1)

            # Second tick: the code and the test result are already recorded, so
            # the tick re-enters at publish and spends no tokens.
            await self.run_tick()

        self.assertEqual(self.agent.ainvoke.await_count, 1)
        self.assertEqual(self.stored()["attempts"]["publish"], 2)
        self.assertEqual(self.stored()["attempts"]["implement"], 1)

    async def test_a_crash_after_verify_resumes_at_publish_with_zero_llm_calls(self):
        # The state a reclaim leaves behind: queued, no lease, stage preserved.
        self.write_manifest([_task(
            stage="verified", verified_digest="digest_1", impl_digest="digest_1",
            status="queued", run_id="run_R", branch_name="feat/demo/t1"
        )])
        os.makedirs(os.path.join("workspaces", "runs", "run_R"), exist_ok=True)
        self.addCleanup(lambda: os.rmdir(os.path.join("workspaces", "runs", "run_R")))

        with patch("graphs.coding.nodes.publish.git_ops.commit_and_push",
                   AsyncMock(return_value=(True, "pushed"))), \
             patch("graphs.coding.nodes.publish.git_ops.create_pull_request",
                   AsyncMock(return_value=(True, "https://github.com/org/repo/pull/9", 9))):
            await self.run_tick()

        self.agent.ainvoke.assert_not_awaited()
        self.assertEqual(self.stored()["status"], "awaiting_review")

    async def test_an_approval_found_on_a_later_tick_merges_and_ends_the_task(self):
        self.write_manifest([_task(
            status="awaiting_review", stage="awaiting_review",
            pr_url="https://github.com/org/repo/pull/7", poll_until=time.time() + 600
        )])
        status = {"state": "OPEN", "labels": [{"name": "approved"}]}

        with patch("graphs.coding.nodes.sync_review.get_push_identity", return_value=None), \
             patch("graphs.coding.nodes.sync_review._teardown", AsyncMock()), \
             patch("graphs.coding.nodes.sync_review.git_ops.get_pull_request_status",
                   AsyncMock(return_value=status)), \
             patch("graphs.coding.nodes.sync_review.git_ops.merge_pull_request",
                   AsyncMock(return_value=(True, "https://github.com/org/repo/commit/abc", "merged"))):
            state = await self.run_tick()

        self.assertEqual(self.stored()["status"], "done")
        self.assertIn("merged", "\n".join(state["tick_report"]))

    async def test_one_tick_never_works_the_same_task_twice(self):
        """sync_review routes back to the scheduler; without tick_handled that loops."""
        self.write_manifest([_task(
            status="awaiting_review", stage="awaiting_review",
            pr_url="https://github.com/org/repo/pull/7", poll_until=time.time() + 600
        )])
        pr_status = AsyncMock(return_value={"state": "OPEN"})

        with patch("graphs.coding.nodes.sync_review.get_push_identity", return_value=None), \
             patch("graphs.coding.nodes.sync_review.git_ops.get_pull_request_status", pr_status):
            state = await self.run_tick()

        self.assertEqual(pr_status.await_count, 1)
        self.assertEqual(state["tick_report"], [])


if __name__ == "__main__":
    unittest.main()


