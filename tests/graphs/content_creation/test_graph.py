import unittest
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.loaders.graphs_loader import GraphsLoader
from graphs.content_creation.graph import create_graph

class TestGraph(unittest.IsolatedAsyncioTestCase):
    def test_graphs_loader_discovery(self):
        loader = GraphsLoader()
        graph_info = loader.get_graph("content_creation")
        self.assertIsNotNone(graph_info)
        self.assertEqual(graph_info["metadata"].get("name"), "content_creation")
        self.assertIsNotNone(graph_info["create_graph"])

    def test_modular_3_macro_node_graph_structure(self):
        graph = create_graph()
        nodes = set(graph.nodes.keys())
        
        # Verify the 3 high-cohesion macro nodes + ask_for_audio + explicit gate processors
        self.assertIn("ingest_audio", nodes)
        self.assertIn("ask_for_audio", nodes)
        self.assertIn("ideate_package", nodes)
        self.assertIn("process_gate1_decision", nodes)
        self.assertIn("produce_deliverables", nodes)
        self.assertIn("process_gate2_decision", nodes)
        user_nodes = nodes - {"__start__", "__end__"}
        self.assertEqual(len(user_nodes), 6)

    async def test_hitl_gate1_image_revision_feedback_loop(self):
        from unittest.mock import patch, AsyncMock
        from langgraph.checkpoint.memory import MemorySaver
        from graphs.content_creation.graph import create_graph
        from langchain_core.messages import HumanMessage

        checkpointer = MemorySaver()
        graph = create_graph(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test_hitl_loop"}}

        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            v1_img = os.path.join(temp_dir, "cat_image.jpg")
            with open(v1_img, "wb") as f: f.write(b"V1")
            v1_plot = os.path.join(temp_dir, "cat_video_plot.md")
            with open(v1_plot, "w") as f: f.write("Plot v1")

            v2_img = os.path.join(temp_dir, "cat_image_v2.jpg")
            v2_plot = os.path.join(temp_dir, "cat_video_plot_v2.md")

            initial_state = {
                "project_path": temp_dir,
                "output_path": temp_dir,
                "topic": "cat",
                "style": "3D",
                "source_audio_path": "tests/fake_audio.m4a",
                "image_path": v1_img,
                "video_plot_path": v1_plot,
                "gate1_decision": "approved",
                "latest_human_feedback": ""
            }

            # Simulate first pass pausing at Gate 1
            with patch("graphs.content_creation.nodes.ingestion.ingest_audio_node.ingest_audio_node", new=AsyncMock(return_value={"source_audio_path": "tests/fake_audio.m4a"})):
                with patch("graphs.content_creation.nodes.ideation.ideate_package_node.generate_image_task", new=AsyncMock(return_value={"image_path": v1_img})) as mock_gen_img_1:
                    with patch("graphs.content_creation.nodes.ideation.ideate_package_node.draft_plot_task", new=AsyncMock(return_value={"video_plot_path": v1_plot})):
                        with patch("graphs.content_creation.nodes.ideation.ideate_package_node.audit_plot_task", new=AsyncMock(return_value={"video_plot_qc_passed": True})):
                            state1 = await graph.ainvoke(initial_state, config=config)
                            self.assertEqual(state1["image_path"], v1_img)
                            mock_gen_img_1.assert_called_once()

            # Check that graph paused after ideate_package (Gate 1), next scheduled node is process_gate1_decision
            snapshot = graph.get_state(config)
            self.assertEqual(snapshot.next, ("process_gate1_decision",))

            # Simulate User providing revision feedback at HITL Gate 1 (as graph_call does)
            user_feedback = "Use reference image and character/ayla_3d.jpg. have ayla wear a cat costume, in the post of pretending like a cat crawling on the floor. Do not include any actual cats in the image."
            graph.update_state(config, {
                "latest_human_feedback": user_feedback,
                "messages": [HumanMessage(content=user_feedback)]
            }, as_node="ideate_package")

            async def fake_img_v2(state):
                with open(v2_img, "wb") as f: f.write(b"V2")
                return {"image_path": v2_img}

            async def fake_plot_v2(state):
                with open(v2_plot, "w") as f: f.write("Plot v2")
                return {"video_plot_path": v2_plot}

            # Resume graph - verify it routes back to ideate_package and calls generate_image_task with revision
            with patch("graphs.content_creation.nodes.ideation.ideate_package_node.generate_image_task", new=AsyncMock(side_effect=fake_img_v2)) as mock_gen_img_2:
                with patch("graphs.content_creation.nodes.ideation.ideate_package_node.draft_plot_task", new=AsyncMock(side_effect=fake_plot_v2)):
                    with patch("graphs.content_creation.nodes.ideation.ideate_package_node.audit_plot_task", new=AsyncMock(return_value={"video_plot_qc_passed": True})):
                        state2 = await graph.ainvoke(None, config=config)
                        # Verified image was revised to v2!
                        self.assertEqual(state2["image_path"], v2_img)
                        self.assertEqual(state2["video_plot_path"], v2_plot)
                        mock_gen_img_2.assert_called_once()

                        # Verify working state had the classified intent
                        called_state = mock_gen_img_2.call_args[0][0]
                        self.assertEqual(called_state.get("gate1_decision"), "revise_image")
                        self.assertEqual(called_state.get("latest_human_feedback"), user_feedback)

if __name__ == "__main__":
    unittest.main()
