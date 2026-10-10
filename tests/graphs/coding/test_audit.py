"""The advisory review pass in front of publish.

The audit annotates and never sends the work back, and an unreachable model is
not even an annotation. Treating an outage as a rejection would put a message
about the auditor, not the code, on every PR the provider hiccupped during.
"""
from unittest.mock import AsyncMock, patch

from graphs.coding.nodes.audit import audit_node
from tests.graphs.coding.fixtures import ManifestFixture, _task


class TestAuditNode(ManifestFixture):
    async def test_advisory_rejection_still_publishes(self):
        task = _task(stage="verified")
        self.write_manifest([task])
        verdict = {"passed": False, "feedback": "magic numbers"}

        with patch("graphs.coding.nodes.audit.git_ops.get_branch_diff", AsyncMock(return_value="diff")), \
             patch("graphs.coding.nodes.audit._run_audit", AsyncMock(return_value=verdict)):
            result = await audit_node(self.base_state(task))

        self.assertNotEqual(result.get("route"), "implement")
        self.assertEqual(result["route"], "publish")
        self.assertEqual(result["audit_feedback"], "magic numbers")
        self.assertEqual(self.stored()["stage"], "audited")

    async def test_an_llm_outage_is_not_a_rejection(self):
        from graphs.coding.nodes.audit import _run_audit

        with patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock(side_effect=RuntimeError("model down"))
            verdict = await _run_audit(spec_content="spec", diff="d", channel="c")

        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["feedback"], "")

    async def test_the_committed_branch_is_reviewed_against_the_default_branch(self):
        """After the push `git diff HEAD` is empty; the branch diff is the change."""
        task = _task(stage="verified", head_sha="s1")
        self.write_manifest([task])

        with patch("graphs.coding.nodes.audit.git_ops.get_branch_diff",
                   AsyncMock(return_value="diff")) as mock_diff, \
             patch("graphs.coding.nodes.audit._run_audit",
                   AsyncMock(return_value={"passed": True, "feedback": ""})) as mock_audit:
            await audit_node(self.base_state(task))

        self.assertEqual(mock_diff.await_args.args[1], "origin/main")
        self.assertEqual(mock_audit.await_args.kwargs["diff"], "diff")
        self.assertEqual(self.stored()["audited_sha"], "s1")

    async def test_the_same_commit_is_not_audited_twice(self):
        task = _task(stage="audited", head_sha="s1", audited_sha="s1")
        self.write_manifest([task])

        with patch("graphs.coding.nodes.audit._run_audit", AsyncMock()) as mock_audit:
            result = await audit_node(self.base_state(task))

        mock_audit.assert_not_called()
        self.assertEqual(result["route"], "publish")

    async def test_a_new_commit_is_audited_again(self):
        task = _task(stage="audited", head_sha="s2", audited_sha="s1")
        self.write_manifest([task])

        with patch("graphs.coding.nodes.audit.git_ops.get_branch_diff", AsyncMock(return_value="d")), \
             patch("graphs.coding.nodes.audit._run_audit",
                   AsyncMock(return_value={"passed": True, "feedback": ""})) as mock_audit:
            await audit_node(self.base_state(task))

        mock_audit.assert_awaited_once()
        self.assertEqual(self.stored()["audited_sha"], "s2")
