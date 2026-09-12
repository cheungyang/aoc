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

        with patch("graphs.coding.nodes.audit.git_ops.get_git_diff", AsyncMock(return_value="diff")), \
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
