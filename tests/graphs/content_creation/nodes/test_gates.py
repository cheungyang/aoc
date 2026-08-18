import unittest
import tempfile
import os
from graphs.content_creation.nodes.gates import process_gate1_node, process_gate2_node


class TestGateNodes(unittest.IsolatedAsyncioTestCase):

    async def test_process_gate1_node_classifies_and_records_decision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = {
                "topic": "cat",
                "output_dir": temp_dir,
                "latest_human_feedback": "Please have Ayla wear a dinosaur costume instead of normal clothes."
            }
            res = await process_gate1_node(state)
            self.assertEqual(res["gate1_decision"], "revise_image")

    async def test_process_gate1_node_approves_when_feedback_is_lgtm(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = {
                "topic": "cat",
                "output_dir": temp_dir,
                "latest_human_feedback": "looks great, proceed"
            }
            res = await process_gate1_node(state)
            self.assertEqual(res["gate1_decision"], "approved")

    async def test_process_gate2_node_classifies_copy_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = {
                "topic": "cat",
                "output_dir": temp_dir,
                "latest_human_feedback": "Update the captions to mention the粵語 pronunciation."
            }
            res = await process_gate2_node(state)
            self.assertEqual(res["gate2_decision"], "revise_copy")

    async def test_process_gate2_node_classifies_video_reanimation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = {
                "topic": "cat",
                "output_dir": temp_dir,
                "latest_human_feedback": "Re-render the video animation with smoother camera movement."
            }
            res = await process_gate2_node(state)
            self.assertEqual(res["gate2_decision"], "revise_video")


if __name__ == "__main__":
    unittest.main()
