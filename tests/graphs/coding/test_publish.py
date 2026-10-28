"""The pull request, and the poll window it opens.

The branch is already pushed by the time publish runs (the `push` node does it,
straight after implement), so publishing only talks to the PR API. It fails in
ways no worker can fix: a failed PR call never throws the pushed branch away, a
transient failure retries at publish rather than re-implementing, and the lease
is always handed back — otherwise the next tick cannot see the task for another
thirty minutes. It also refuses to open a PR for a commit verify did not pass.
"""
import time
from unittest.mock import AsyncMock, patch

from core.git.privacy import NotesMissing
from graphs.coding.nodes.publish import MAX_PUBLISH_ATTEMPTS, NOT_CHECKED, WITHHELD, publish_node
from tests.graphs.coding.fixtures import ManifestFixture, _task as _base_task

PR_URL = "https://github.com/org/repo/pull/7"
SUMMARY = "Added the parser; its tests use made up words that stand in for a copied line."
AUDIT = "- [Happy Path Bias] core/a.py: no test for an empty file"


def _task(**overrides):
    """A task as publish meets it: audited, and verified at its pushed commit."""
    return _base_task(**{
        "stage": "audited", "head_sha": "sha1", "verified_sha": "sha1", **overrides
    })


class TestPublishNode(ManifestFixture):
    def setUp(self):
        super().setUp()
        self.review_context = AsyncMock()
        # The PR text check reads the notes; here it is stubbed clean unless a
        # test says otherwise.
        self.guard = patch("graphs.coding.nodes.publish.privacy_guard.check", return_value=[])
        for p in (
            patch("graphs.coding.nodes.publish.get_push_identity", return_value=None),
            patch("graphs.coding.nodes.publish._post_review_context", self.review_context),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.guard_check = self.guard.start()
        self.addCleanup(self.guard.stop)

    def _pr(self, find=("", None), create=(True, PR_URL, 7)):
        return (
            patch("graphs.coding.nodes.publish._find_open_pr", AsyncMock(return_value=find)),
            patch("graphs.coding.nodes.publish.git_ops.create_pull_request",
                  AsyncMock(return_value=create)),
        )

    async def test_happy_path_opens_the_poll_window(self):
        task = _task()
        self.write_manifest([task])

        find, create = self._pr()
        with find, create:
            result = await publish_node(self.base_state(task))

        stored = self.stored()
        self.assertEqual(result["pr_url"], PR_URL)
        self.assertEqual(stored["status"], "awaiting_review")
        self.assertEqual(stored["head_sha"], "sha1")
        self.assertGreater(stored["poll_until"], time.time())
        # The lease is released so a later tick may sync the review.
        self.assertIsNone(stored["lease_owner"])

    async def test_publish_never_pushes(self):
        """Pushing is the `push` node's job; publish only upserts the PR."""
        task = _task()
        self.write_manifest([task])

        find, create = self._pr()
        with find, create, patch("graphs.coding.nodes.publish.git_ops.commit_and_push",
                                 AsyncMock()) as mock_push:
            await publish_node(self.base_state(task))

        mock_push.assert_not_called()

    async def test_an_unverified_commit_is_sent_back_to_verify(self):
        """A PR must show code that passed verification, not a later commit."""
        task = _task(verified_sha="sha0")
        self.write_manifest([task])

        find, create = self._pr()
        with find, create as mock_create:
            result = await publish_node(self.base_state(task))

        mock_create.assert_not_called()
        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["stage"], "pushed")
        self.assertIsNone(self.stored()["lease_owner"])

    async def test_nothing_pushed_is_sent_back_to_push(self):
        task = _task(head_sha=None, verified_sha=None)
        self.write_manifest([task])

        result = await publish_node(self.base_state(task))

        self.assertEqual(result["route"], "done")
        self.assertEqual(self.stored()["stage"], "implemented")

    async def test_an_existing_pr_for_the_branch_is_adopted_not_duplicated(self):
        task = _task()
        self.write_manifest([task])

        find, create = self._pr(find=("https://github.com/org/repo/pull/4", 4))
        with find, create as mock_create:
            result = await publish_node(self.base_state(task))

        mock_create.assert_not_called()
        self.assertEqual(result["pr_url"], "https://github.com/org/repo/pull/4")

    async def test_a_failed_pr_reports_a_compare_url(self):
        """Never throw away a pushed branch; hand back a link that works."""
        task = _task()
        self.write_manifest([task])

        find, create = self._pr(create=(False, "gh: rate limited", None))
        with find, create:
            result = await publish_node(self.base_state(task))

        joined = "\n".join(result["tick_report"])
        self.assertIn("https://github.com/org/repo/compare/feat/demo/t1?expand=1", joined)
        self.assertEqual(self.stored()["head_sha"], "sha1")

    async def test_a_transient_failure_retries_on_the_next_tick(self):
        task = _task()
        self.write_manifest([task])

        find, create = self._pr(create=(False, "network down", None))
        with find, create:
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
        task = _task(attempts={"implement": 2})
        self.write_manifest([task])

        find, create = self._pr(create=(False, "network down", None))
        with find, create:
            await publish_node(self.base_state(task))

        self.assertEqual(self.stored()["attempts"]["implement"], 2)
        self.assertEqual(self.stored()["attempts"]["publish"], 1)

    async def test_publish_halts_once_its_own_budget_is_gone(self):
        task = _task(attempts={"publish": MAX_PUBLISH_ATTEMPTS - 1})
        self.write_manifest([task])

        find, create = self._pr(create=(False, "network down", None))
        with find, create:
            await publish_node(self.base_state(task))

        self.assertEqual(self.stored()["status"], "halted")

    # -- The PR text an LLM wrote ------------------------------------------------

    def _state(self, task, **text):
        return self.base_state(task, implementation_summary=SUMMARY, audit_feedback=AUDIT, **text)

    async def test_only_the_text_an_llm_wrote_is_checked(self):
        task = _task()
        self.write_manifest([task])

        find, create = self._pr()
        with find, create:
            await publish_node(self._state(task))

        texts = list(self.guard_check.call_args.args[0])
        self.assertEqual(texts, [("PR summary", SUMMARY), ("audit comment", AUDIT)])

    async def test_clean_text_is_posted_as_written(self):
        task = _task()
        self.write_manifest([task])

        find, create = self._pr()
        with find, create as mock_create:
            result = await publish_node(self._state(task))

        self.assertIn(SUMMARY, mock_create.call_args.kwargs["body"])
        self.assertEqual(self.review_context.call_args.kwargs["state"]["audit_feedback"], AUDIT)
        self.assertNotIn("🔒", "\n".join(result["tick_report"]))

    async def test_a_summary_that_looks_personal_is_withheld_and_the_pr_still_opens(self):
        self.guard_check.return_value = [
            ("PR summary", 1, "\u201cmade up words that stand in for a copied line\u201d")
        ]
        task = _task()
        self.write_manifest([task])

        find, create = self._pr()
        with find, create as mock_create:
            result = await publish_node(self._state(task))

        body = mock_create.call_args.kwargs["body"]
        self.assertIn(WITHHELD, body)
        self.assertNotIn("stand in for", body)
        # The audit text was clean, so it is posted as written.
        self.assertEqual(self.review_context.call_args.kwargs["state"]["audit_feedback"], AUDIT)
        self.assertEqual(self.stored()["status"], "awaiting_review")
        # The words are yours to see in the report, never on GitHub.
        report = "\n".join(result["tick_report"])
        self.assertIn("🔒 `T1`: withheld the PR summary", report)
        self.assertIn("stand in for", report)

    async def test_audit_text_that_looks_personal_is_withheld_from_the_comment(self):
        self.guard_check.return_value = [("audit comment", 1, "email pat@example.com")]
        task = _task()
        self.write_manifest([task])

        find, create = self._pr()
        with find, create as mock_create:
            await publish_node(self._state(task))

        self.assertEqual(self.review_context.call_args.kwargs["state"]["audit_feedback"], WITHHELD)
        self.assertIn(SUMMARY, mock_create.call_args.kwargs["body"])

    async def test_without_notes_the_text_is_withheld_not_posted_unchecked(self):
        self.guard_check.side_effect = NotesMissing("no personal notes under /nowhere")
        task = _task()
        self.write_manifest([task])

        find, create = self._pr()
        with find, create as mock_create:
            result = await publish_node(self._state(task))

        self.assertIn(NOT_CHECKED, mock_create.call_args.kwargs["body"])
        self.assertNotIn(SUMMARY, mock_create.call_args.kwargs["body"])
        self.assertEqual(self.review_context.call_args.kwargs["state"]["audit_feedback"], NOT_CHECKED)
        self.assertEqual(self.stored()["status"], "awaiting_review")
        self.assertIn("can't find your notes", "\n".join(result["tick_report"]))
