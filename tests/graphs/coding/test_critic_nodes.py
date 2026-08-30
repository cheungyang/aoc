import unittest
from unittest.mock import patch, AsyncMock
from graphs.coding.schemas import CodingState
from graphs.coding.nodes.critic_node import critic_node


class TestCriticNode(unittest.IsolatedAsyncioTestCase):
    @patch('graphs.coding.nodes.critic_node.git_ops.get_git_diff', new_callable=AsyncMock)
    async def test_critic_node_approve(self, mock_diff):
        mock_diff.return_value = "diff --git a/test.py b/test.py\n+ def test(): pass"
        with patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock(return_value="""
            <critic_verdict>
              <verdict>APPROVE</verdict>
              <anti_patterns_detected></anti_patterns_detected>
              <feedback_for_worker>Looks good.</feedback_for_worker>
            </critic_verdict>
            """)
            state: CodingState = {
                "workspace_path": "/tmp/ws",
                "modified_files": ["test.py"],
                "current_task": {
                    "task_id": "T-1",
                    "acceptance_criteria": "Given test When run Then pass"
                }
            }
            res = await critic_node(state)
            self.assertTrue(res["critic_passed"])
            self.assertEqual(res["critic_feedback"], "")

    @patch('graphs.coding.nodes.critic_node.git_ops.get_git_diff', new_callable=AsyncMock)
    async def test_critic_node_fails_closed_when_agent_fails(self, mock_diff):
        mock_diff.return_value = "diff --git a/test.py b/test.py\n+ def test(): pass"
        with patch("tools.agent_call.agent_call") as mock_agent:
            mock_agent.ainvoke = AsyncMock(side_effect=RuntimeError("LLM timeout"))
            state: CodingState = {
                "workspace_path": "/tmp/ws",
                "modified_files": ["test.py"],
                "current_task": {
                    "task_id": "T-1",
                    "acceptance_criteria": "Given test When run Then pass"
                }
            }
            res = await critic_node(state)
            self.assertFalse(res["critic_passed"])
            self.assertIn("Critic QA audit failed to complete", res["critic_feedback"])


if __name__ == "__main__":
    unittest.main()
