"""The compiled graph and the boundaries between its nodes.

The graph must refuse the checkpointer the loader hands it, because durable
state lives in the manifest, git and GitHub rather than in a caller's session —
honouring it is what left a halted run resumable from nowhere else. It must
also keep the LLM and the remote in separate nodes, so a GitHub failure resumes
at the push instead of re-running the worker. Only a run through the compiled
graph catches a route that no declared channel carries.
"""
import os
import time
import unittest
from unittest.mock import AsyncMock, patch

from tests.graphs.coding.fixtures import ManifestFixture, _task


class TestGraphTopology(unittest.TestCase):
    def test_the_graph_compiles_without_a_checkpointer(self):
        """Durable state lives in the manifest, git and GitHub — not a checkpoint."""
        from graphs.coding.graph import create_graph

        graph = create_graph()
        self.assertIsNone(graph.checkpointer)
        # Exact set, not a superset: this is also what keeps the interrupt-driven
        # topology (`hitl_gate`, `worker_node`, `git_handoff`) from coming back.
        self.assertEqual(
            {"scheduler", "implement", "verify", "audit", "publish", "sync_review"},
            set(graph.nodes) - {"__start__"},
        )

    def test_a_supplied_checkpointer_is_ignored_not_honoured(self):
        """The loader hands every graph a checkpointer; this one must refuse it.

        Honouring it would key durable state to the caller's session again,
        which is the reason a halted run could not be resumed from anywhere
        else (C4/C5).
        """
        from langgraph.checkpoint.memory import MemorySaver
        from graphs.coding.graph import create_graph

        graph = create_graph(checkpointer=MemorySaver())

        self.assertIsNone(graph.checkpointer)


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
        return await create_graph().ainvoke(self.inputs())

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


class TestTheImplementVerifyLoopTerminates(TestTickEndToEnd):
    """The budget has to hold *within* one tick, not only across ticks.

    implement and verify hand a task back and forth, and both read the budget
    off `current_task`. That is the scheduler's opening snapshot, so while the
    nodes only wrote the new count to disk, verify saw zero attempts every time
    round and routed straight back to implement. The tick ran the worker 25
    times on one task and died on LangGraph's recursion limit — which is what
    put the same `<worker_handoff>` block in the channel 25 times.

    Only a run through the compiled graph catches this: called directly, each
    node is given a fresh, correct `current_task` by the test itself.
    """

    def setUp(self):
        super().setUp()
        # A worker that always produces a new tree, and tests that never pass:
        # the loop's worst case, and the shape of the incident.
        self.digests = iter(f"digest_{n}" for n in range(1, 100))
        patch("graphs.coding.nodes.implement.compute_worktree_digest",
              AsyncMock(side_effect=lambda *_a, **_k: next(self.digests))).start()
        patch("graphs.coding.nodes.implement.digest_matches",
              AsyncMock(return_value=False)).start()

    async def test_a_task_whose_tests_never_pass_stops_at_the_budget(self):
        from graphs.coding.nodes.implement import MAX_IMPLEMENT_ATTEMPTS

        self.write_manifest([_task(stage="queued")])

        with patch("graphs.coding.nodes.verify._run",
                   AsyncMock(return_value=(1, "", "AssertionError: expected 3 to equal 4"))):
            await self.run_tick()

        self.assertEqual(self.agent.ainvoke.await_count, MAX_IMPLEMENT_ATTEMPTS)
        self.assertEqual(self.stored()["status"], "halted")
        self.assertEqual(self.stored()["attempts"]["implement"], MAX_IMPLEMENT_ATTEMPTS)

    async def test_a_worktree_with_no_dependencies_costs_exactly_one_llm_call(self):
        """The incident itself: `npx vitest` with no `node_modules`.

        The command cannot run, so its output says nothing about the code. One
        attempt is enough to learn that; the other 24 were spent rewriting a
        correct implementation.
        """
        self.write_manifest([_task(stage="queued")])

        with patch("graphs.coding.nodes.verify._run",
                   AsyncMock(return_value=(1, "", "Error: Cannot find module 'react'"))):
            await self.run_tick()

        self.assertEqual(self.agent.ainvoke.await_count, 1)
        self.assertEqual(self.stored()["status"], "halted")
        self.assertEqual(self.stored()["last_error"]["kind"], "environment")


if __name__ == "__main__":
    unittest.main()
