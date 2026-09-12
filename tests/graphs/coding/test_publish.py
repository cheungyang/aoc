"""The push, the pull request, and the poll window they open.

Publishing is the node that touches the remote, so it fails in ways no worker
can fix. A successful push is never thrown away even when the pull request
call fails, a transient failure retries at publish rather than re-implementing,
and the lease is always handed back — otherwise the next tick cannot see the
task for another thirty minutes.
"""
import time
from unittest.mock import AsyncMock, patch

from graphs.coding.nodes.publish import MAX_PUBLISH_ATTEMPTS, publish_node
from tests.graphs.coding.fixtures import ManifestFixture, _task


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
