import unittest
import os
import tempfile
import json
from unittest.mock import patch, AsyncMock, MagicMock
from langgraph.checkpoint.memory import MemorySaver
from graphs.coding.graph import create_graph
from graphs.coding.adapters import prepare_input, format_output
from graphs.coding.nodes.hitl_gate import classify_hitl_intent

class TestCodingHandoffAndBranching(unittest.IsolatedAsyncioTestCase):
    """
    Exhaustive integration tests for:
    1. True XML handoff data integrity across nodes (Worker XML -> Tester -> Critic XML -> HITL -> Handoff).
    2. Dynamic HITL intent branching (approval variants, revision variants, abort variants).
    3. Retry loops and max retry limits for Tester and Critic.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manifest_path = os.path.join(self.temp_dir.name, "build_request.json")
        self.spec_path = os.path.join(self.temp_dir.name, "specs", "feature.md")
        os.makedirs(os.path.dirname(self.spec_path), exist_ok=True)
        
        with open(self.spec_path, "w") as f:
            f.write("# Feature Spec\nAllowed files: app.py\nSchema: Payload\nGiven input When run Then ok\nVerification: pytest")

        self.manifest_data = {
            "version": "1.0",
            "project_name": "handoff_app",
            "max_concurrency": 1,
            "queue": [
                {
                    "task_id": "TASK-HANDOFF-01",
                    "project_name": "handoff_app",
                    "feature_name": "core_feature",
                    "spec_path": self.spec_path,
                    "dependencies": [],
                    "allowed_files": ["app.py"],
                    "verification_command": "pytest",
                    "acceptance_criteria": "Given payload When run Then true",
                    "status": "pending"
                }
            ]
        }
        with open(self.manifest_path, "w") as f:
            json.dump(self.manifest_data, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_full_xml_handoff_chain(self):
        """Tests that raw XML emitted by Worker and Critic is properly parsed and propagated through the graph."""
        async def fake_provision(repo_path, workspace_path, branch_name, base_ref=None):
            os.makedirs(workspace_path, exist_ok=True)
            return (True, "Worktree provisioned")

        with patch("graphs.coding.nodes.provisioner.git_ops.provision_worktree", side_effect=fake_provision), \
             patch("graphs.coding.nodes.critic_node.git_ops.get_git_diff", new_callable=AsyncMock) as mock_diff, \
             patch("graphs.coding.nodes.git_handoff.git_ops.create_pull_request", new_callable=AsyncMock) as mock_pr, \
             patch("graphs.coding.nodes.git_handoff.git_ops.commit_and_push", new_callable=AsyncMock) as mock_commit, \
             patch("graphs.coding.nodes.git_handoff.git_ops.teardown_worktree", new_callable=AsyncMock) as mock_teardown, \
             patch("tools.agent_call.agent_call") as mock_agent:

            mock_diff.return_value = "diff --git a/app.py b/app.py\n+ def core(): return True"
            mock_commit.return_value = (True, "Committed")
            mock_pr.return_value = (True, "https://github.com/org/repo/pull/500")
            mock_teardown.return_value = (True, "Cleaned")

            # Worker raw XML and Critic raw XML
            raw_worker_xml = """
            <worker_handoff>
              <status>READY_FOR_TEST</status>
              <modified_files>
                <file>app.py</file>
              </modified_files>
              <implementation_summary>Completed core logic for payload.</implementation_summary>
            </worker_handoff>
            """
            raw_critic_xml = """
            <critic_verdict>
              <verdict>APPROVE</verdict>
              <anti_patterns_detected></anti_patterns_detected>
              <feedback_for_worker>Diff conforms to spec without anti-patterns.</feedback_for_worker>
            </critic_verdict>
            """

            mock_agent.ainvoke = AsyncMock(side_effect=[raw_worker_xml, raw_critic_xml])

            graph = create_graph(checkpointer=MemorySaver())
            inputs = prepare_input(
                query="Run build",
                build_request_path=self.manifest_path,
                project_path=self.temp_dir.name,
                thread_id="test_xml_handoff"
            )
            config = {"configurable": {"thread_id": "test_xml_handoff"}}

            with patch('graphs.coding.nodes.tester_node.asyncio.create_subprocess_shell') as mock_subproc:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.communicate = AsyncMock(return_value=(b"2 passed in 0.1s", b""))
                mock_subproc.return_value = mock_proc

                paused_state = await graph.ainvoke(inputs, config=config)

            # Verify that parsed worker data reached state
            self.assertEqual(paused_state["modified_files"], ["app.py"])
            self.assertEqual(paused_state["implementation_summary"], "Completed core logic for payload.")
            self.assertTrue(paused_state["test_run_passed"])
            self.assertEqual(paused_state["test_stdout"], "2 passed in 0.1s")
            self.assertTrue(paused_state["critic_passed"])

            # Verify presentation format
            presentation = format_output(paused_state)
            self.assertIn("TASK-HANDOFF-01", presentation)
            self.assertIn("ALL TESTS PASSING", presentation)

            # Resume with approve
            await graph.aupdate_state(config, {"latest_human_feedback": "Looks good, proceed", "hitl_decision": "approved"})
            resumed = await graph.ainvoke(None, config=config)
            self.assertEqual(resumed["pr_url"], "https://github.com/org/repo/pull/500")
            self.assertIn("TASK-HANDOFF-01", resumed["completed_tasks"])

    async def test_dynamic_hitl_intent_classification(self):
        """Tests that human replies are robustly categorized into approved, revise, or abort."""
        # Approval variations
        self.assertEqual(classify_hitl_intent("Approve"), "approved")
        self.assertEqual(classify_hitl_intent("LGTM"), "approved")
        self.assertEqual(classify_hitl_intent("looks great, good to merge"), "approved")
        self.assertEqual(classify_hitl_intent("yes, proceed"), "approved")
        self.assertEqual(classify_hitl_intent("ok"), "approved")

        # Abort variations
        self.assertEqual(classify_hitl_intent("abort"), "abort")
        self.assertEqual(classify_hitl_intent("cancel execution"), "abort")
        self.assertEqual(classify_hitl_intent("stop"), "abort")

        # Revision variations
        self.assertEqual(classify_hitl_intent("Please fix error handling on line 42"), "revise")
        self.assertEqual(classify_hitl_intent("Add docstrings to auth module"), "revise")
        self.assertEqual(classify_hitl_intent("Change parameter type to string"), "revise")
        self.assertEqual(classify_hitl_intent("We found a bug in edge case handling"), "revise")

    async def test_hitl_revision_feedback_loop_to_worker(self):
        """Tests that user requesting revisions at HITL gate loops back to worker with feedback injected."""
        async def fake_provision(repo_path, workspace_path, branch_name, base_ref=None):
            os.makedirs(workspace_path, exist_ok=True)
            return (True, "Worktree provisioned")

        with patch("graphs.coding.nodes.provisioner.git_ops.provision_worktree", side_effect=fake_provision), \
             patch("graphs.coding.nodes.critic_node.git_ops.get_git_diff", new_callable=AsyncMock) as mock_diff, \
             patch("tools.agent_call.agent_call") as mock_agent:

            mock_diff.return_value = "diff --git a/app.py b/app.py\n+ def core(): return True"

            # Iteration 1: Worker -> Critic
            # Iteration 2 (after human revision): Worker -> Critic
            mock_agent.ainvoke = AsyncMock(side_effect=[
                "<worker_handoff><status>READY_FOR_TEST</status><modified_files><file>app.py</file></modified_files><implementation_summary>Attempt 1.</implementation_summary></worker_handoff>",
                "<critic_verdict><verdict>APPROVE</verdict><anti_patterns_detected></anti_patterns_detected><feedback_for_worker></feedback_for_worker></critic_verdict>",
                "<worker_handoff><status>READY_FOR_TEST</status><modified_files><file>app.py</file></modified_files><implementation_summary>Attempt 2 with docstrings.</implementation_summary></worker_handoff>",
                "<critic_verdict><verdict>APPROVE</verdict><anti_patterns_detected></anti_patterns_detected><feedback_for_worker></feedback_for_worker></critic_verdict>"
            ])

            graph = create_graph(checkpointer=MemorySaver())
            inputs = prepare_input(
                query="Run build",
                build_request_path=self.manifest_path,
                project_path=self.temp_dir.name,
                thread_id="test_hitl_revision"
            )
            config = {"configurable": {"thread_id": "test_hitl_revision"}}

            with patch('graphs.coding.nodes.tester_node.asyncio.create_subprocess_shell') as mock_subproc:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.communicate = AsyncMock(return_value=(b"passed", b""))
                mock_subproc.return_value = mock_proc

                paused_1 = await graph.ainvoke(inputs, config=config)

            self.assertEqual(paused_1["hitl_decision"], "pending_review")

            # Human requests revisions
            await graph.aupdate_state(config, {"latest_human_feedback": "Please add docstrings to core function", "hitl_decision": "revise"})

            # Resume execution - should loop through worker, tester, critic and pause at HITL again
            with patch('graphs.coding.nodes.tester_node.asyncio.create_subprocess_shell') as mock_subproc:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.communicate = AsyncMock(return_value=(b"passed", b""))
                mock_subproc.return_value = mock_proc

                paused_2 = await graph.ainvoke(None, config=config)

            self.assertEqual(paused_2["hitl_decision"], "pending_review")
            self.assertEqual(paused_2["implementation_summary"], "Attempt 2 with docstrings.")

    async def test_critic_rejection_retry_loop(self):
        """Tests that when Critic rejects diff, graph loops back to Worker with anti-pattern feedback."""
        async def fake_provision(repo_path, workspace_path, branch_name, base_ref=None):
            os.makedirs(workspace_path, exist_ok=True)
            return (True, "Worktree provisioned")

        with patch("graphs.coding.nodes.provisioner.git_ops.provision_worktree", side_effect=fake_provision), \
             patch("graphs.coding.nodes.critic_node.git_ops.get_git_diff", new_callable=AsyncMock) as mock_diff, \
             patch("tools.agent_call.agent_call") as mock_agent:

            mock_diff.return_value = "diff --git a/app.py b/app.py\n+ def core(): return True"

            # Attempt 1: Worker -> Critic (REJECT)
            # Attempt 2: Worker -> Critic (APPROVE) -> HITL
            mock_agent.ainvoke = AsyncMock(side_effect=[
                "<worker_handoff><status>READY_FOR_TEST</status><modified_files><file>app.py</file></modified_files><implementation_summary>Attempt 1 fake return.</implementation_summary></worker_handoff>",
                """<critic_verdict>
                  <verdict>REJECT</verdict>
                  <anti_patterns_detected>
                    <pattern name="The Fake It Trap">
                      <file>app.py</file>
                      <line_numbers>1-2</line_numbers>
                      <description>Hardcoded boolean return instead of real implementation.</description>
                    </pattern>
                  </anti_patterns_detected>
                  <feedback_for_worker>Implement real logic.</feedback_for_worker>
                </critic_verdict>""",
                "<worker_handoff><status>READY_FOR_TEST</status><modified_files><file>app.py</file></modified_files><implementation_summary>Attempt 2 real logic.</implementation_summary></worker_handoff>",
                "<critic_verdict><verdict>APPROVE</verdict><anti_patterns_detected></anti_patterns_detected><feedback_for_worker>Looks good.</feedback_for_worker></critic_verdict>"
            ])

            graph = create_graph(checkpointer=MemorySaver())
            inputs = prepare_input(
                query="Run build",
                build_request_path=self.manifest_path,
                project_path=self.temp_dir.name,
                thread_id="test_critic_retry"
            )
            config = {"configurable": {"thread_id": "test_critic_retry"}}

            with patch('graphs.coding.nodes.tester_node.asyncio.create_subprocess_shell') as mock_subproc:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.communicate = AsyncMock(return_value=(b"passed", b""))
                mock_subproc.return_value = mock_proc

                paused_state = await graph.ainvoke(inputs, config=config)

            self.assertTrue(paused_state["critic_passed"])
            self.assertEqual(paused_state["implementation_summary"], "Attempt 2 real logic.")
            self.assertEqual(paused_state["attempt_count"], 1)

if __name__ == "__main__":
    unittest.main()
