"""The only node that is allowed to spend an LLM attempt.

The attempt budget is the scarce resource: it must not be spent re-generating a
tree the worker already produced, and it must not be counted as progress when
the worker changed nothing. Getting either wrong ends with a task that burns
its whole budget on a no-op, or one that loops forever without advancing.
"""
from unittest.mock import AsyncMock, patch

from graphs.coding.nodes.implement import MAX_IMPLEMENT_ATTEMPTS, implement_node
from tests.graphs.coding.fixtures import ManifestFixture, _task


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
