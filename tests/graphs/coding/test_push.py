"""The push that follows every implement.

Verification tests the commit on GitHub, and a tick on another host resumes
from `origin/<branch>`, so an attempt is not real until it is pushed. The push
must record the SHA it landed, never touch the default branch, retry a GitHub
outage within its own budget, and leave the implement budget alone.

It is also the last stop before GitHub, so nothing the privacy check flags may
be pushed. A hit goes back to the worker with where and what kind, never the
words, and halts the task as `privacy` once the implement budget is spent.
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.util.privacy_guard import NotesMissing
from graphs.coding.nodes.implement import MAX_IMPLEMENT_ATTEMPTS
from graphs.coding.nodes.push import MAX_PUSH_ATTEMPTS, push_node
from graphs.coding.utils.outgoing import OutgoingCheck
from tests.graphs.coding.fixtures import ManifestFixture, _task as _base_task

# Hits as the check returns them. Made up, and placeholders where a pattern
# would match, so this file passes the repo's own privacy test.
RUN_HIT = ("tests/agents/demo/test_new_tool.py", 42, "\u201cmade up words that stand in for a copied line\u201d")
EMAIL_HIT = ("app/settings.py", 7, "email pat@example.com")


def _task(**overrides):
    """A task as push meets it: implemented, not yet pushed."""
    return _base_task(**{"stage": "implemented", "attempts": {"implement": 1}, **overrides})


class TestPushNode(ManifestFixture):
    def setUp(self):
        super().setUp()
        self.push = AsyncMock(return_value=(True, "pushed"))
        self.rev = AsyncMock(return_value="abc1234def")
        # The workspace here is a plain folder, not a git repo: the check itself
        # is tested against real git in test_outgoing.py.
        self.check = AsyncMock(return_value=OutgoingCheck())
        for p in (
            patch("graphs.coding.nodes.push.get_push_identity", return_value=None),
            patch("graphs.coding.nodes.push.git_ops.commit_and_push", self.push),
            patch("graphs.coding.nodes.push.git_ops.rev_parse", self.rev),
            patch("graphs.coding.nodes.push.outgoing.check_outgoing", self.check),
        ):
            p.start()
            self.addCleanup(p.stop)

    async def test_a_push_records_the_sha_and_routes_to_verify(self):
        task = _task()
        self.write_manifest([task])

        result = await push_node(self.base_state(task))

        self.assertEqual(result["route"], "verify")
        self.assertEqual(result["head_sha"], "abc1234def")
        self.assertEqual(result["current_task"]["head_sha"], "abc1234def")
        self.assertEqual(self.stored()["stage"], "pushed")
        self.assertEqual(self.stored()["head_sha"], "abc1234def")

    async def test_each_attempt_is_its_own_commit_on_the_task_branch(self):
        task = _task(attempts={"implement": 2})
        self.write_manifest([task])

        await push_node(self.base_state(task))

        kwargs = self.push.call_args.kwargs
        self.assertEqual(kwargs["branch_name"], "feat/demo/t1")
        self.assertIn("T1", kwargs["commit_msg"])
        self.assertIn("attempt 2", kwargs["commit_msg"])

    async def test_an_already_pushed_task_is_not_pushed_again(self):
        task = _task(stage="pushed", head_sha="s1")
        self.write_manifest([task])

        result = await push_node(self.base_state(task))

        self.push.assert_not_called()
        self.assertEqual(result["route"], "verify")
        self.assertEqual(result["head_sha"], "s1")

    async def test_it_refuses_to_push_to_the_default_branch(self):
        """Attempts may land on the remote only because a task branch contains them."""
        task = _task()
        self.write_manifest([task])

        result = await push_node(self.base_state(task, branch_name="main"))

        self.push.assert_not_called()
        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["status"], "halted")
        self.assertEqual(self.stored()["last_error"]["kind"], "config")

    async def test_a_missing_worktree_yields_without_pushing(self):
        import shutil

        shutil.rmtree(self.workspace)
        task = _task()
        self.write_manifest([task])

        result = await push_node(self.base_state(task))

        self.push.assert_not_called()
        self.assertEqual(result["route"], "done")
        self.assertIsNone(self.stored()["lease_owner"])

    async def test_a_transient_failure_retries_at_push(self):
        self.push.return_value = (False, "network down")
        task = _task()
        self.write_manifest([task])

        result = await push_node(self.base_state(task))

        stored = self.stored()
        self.assertEqual(result["route"], "done")
        self.assertEqual(stored["status"], "queued")
        self.assertIsNone(stored["lease_owner"])
        # Stage kept, so the next tick re-enters here instead of re-implementing.
        self.assertEqual(stored["stage"], "implemented")
        self.assertEqual(stored["attempts"]["push"], 1)
        self.assertEqual(stored["attempts"]["implement"], 1)
        self.assertEqual(stored["last_error"]["kind"], "git")

    async def test_an_unresolvable_head_counts_as_a_failed_push(self):
        self.rev.return_value = ""
        task = _task()
        self.write_manifest([task])

        result = await push_node(self.base_state(task))

        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["stage"], "implemented")
        self.assertEqual(self.stored()["attempts"]["push"], 1)

    async def test_push_halts_once_its_own_budget_is_gone(self):
        self.push.return_value = (False, "network down")
        task = _task(attempts={"implement": 1, "push": MAX_PUSH_ATTEMPTS - 1})
        self.write_manifest([task])

        await push_node(self.base_state(task))

        self.assertEqual(self.stored()["status"], "halted")
        self.assertIsNone(self.stored()["lease_owner"])

    # -- The privacy check -------------------------------------------------------

    async def test_the_check_runs_on_this_task_before_anything_is_pushed(self):
        task = _task()
        self.write_manifest([task])

        with patch("graphs.coding.nodes.push.Config") as config:
            config.return_value.pkm_dir = "/notes"
            await push_node(self.base_state(task))

        self.check.assert_awaited_once_with(self.workspace, "feat/demo/t1", "main", Path("/notes"))
        self.push.assert_awaited_once()

    async def test_a_hit_is_never_pushed_and_goes_back_to_the_worker(self):
        self.check.return_value = OutgoingCheck(hits=[RUN_HIT, EMAIL_HIT])
        task = _task(impl_digest="d1")
        self.write_manifest([task])

        result = await push_node(self.base_state(task))

        self.push.assert_not_called()
        self.assertEqual(result["route"], "implement")
        stored = self.stored()
        self.assertEqual(stored["stage"], "provisioned")
        self.assertIsNone(stored["impl_digest"])
        self.assertEqual(stored["last_error"]["kind"], "privacy")
        # The stage travels with the task, or implement would see an unchanged
        # tree, skip the worker and send it straight back.
        self.assertEqual(result["current_task"]["stage"], "provisioned")
        self.assertIsNone(result["current_task"]["impl_digest"])
        self.assertEqual(stored["attempts"]["implement"], 1, "the next implement spends the attempt")

    async def test_the_worker_hears_where_and_what_never_the_words(self):
        self.check.return_value = OutgoingCheck(hits=[RUN_HIT, EMAIL_HIT])
        task = _task()
        self.write_manifest([task])

        result = await push_node(self.base_state(task))

        findings = result["privacy_findings"]
        self.assertIn("tests/agents/demo/test_new_tool.py:42: 8 words in a row from the user's notes", findings)
        self.assertIn("app/settings.py:7: an email address", findings)
        self.assertNotIn("stand in for", findings)
        self.assertNotIn("pat@example.com", findings)
        # You see the words: the tick report and the manifest never reach GitHub.
        report = "\n".join(result["tick_report"])
        self.assertIn("stand in for", report)
        self.assertIn("2 lines look personal", report)
        self.assertIn("attempt 2/3", report)
        self.assertIn("stand in for", self.stored()["last_error"]["message"])

    async def test_out_of_attempts_it_halts_as_privacy_and_keeps_the_stage(self):
        self.check.return_value = OutgoingCheck(hits=[RUN_HIT])
        task = _task(attempts={"implement": MAX_IMPLEMENT_ATTEMPTS})
        self.write_manifest([task])

        result = await push_node(self.base_state(task))

        self.push.assert_not_called()
        self.assertEqual(result["route"], "done")
        stored = self.stored()
        self.assertEqual(stored["status"], "halted")
        self.assertEqual(stored["last_error"]["kind"], "privacy")
        self.assertIsNone(stored["lease_owner"])
        # `retry` re-enters at push and checks again.
        self.assertEqual(stored["stage"], "implemented")
        self.assertIn("retry T1", result["error_message"])

    async def test_missing_notes_halt_as_config_and_nothing_is_pushed(self):
        self.check.side_effect = NotesMissing("no personal notes under /nowhere")
        task = _task()
        self.write_manifest([task])

        result = await push_node(self.base_state(task))

        self.push.assert_not_called()
        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["status"], "halted")
        self.assertEqual(self.stored()["last_error"]["kind"], "config")
        self.assertIn("PKM_DIR", result["error_message"])

    async def test_a_check_that_cannot_list_the_files_counts_as_a_failed_push(self):
        self.check.return_value = OutgoingCheck(error="git diff failed: not a git repository")
        task = _task()
        self.write_manifest([task])

        result = await push_node(self.base_state(task))

        self.push.assert_not_called()
        stored = self.stored()
        self.assertEqual(result["route"], "done")
        self.assertEqual(stored["status"], "queued")
        self.assertEqual(stored["stage"], "implemented")
        self.assertEqual(stored["attempts"]["push"], 1)
        self.assertEqual(stored["attempts"]["implement"], 1)
        self.assertEqual(stored["last_error"]["kind"], "git")

    async def test_an_already_pushed_attempt_is_not_checked_again(self):
        """It is on GitHub already; there is nothing left to stop."""
        task = _task(stage="pushed", head_sha="s1")
        self.write_manifest([task])

        await push_node(self.base_state(task))

        self.check.assert_not_called()
