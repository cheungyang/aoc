import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys
from langchain_core.messages import AIMessage, HumanMessage

# Inject root directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from graphs.content_creation.graph import (
    create_graph,
    prepare_input,
    format_output,
    should_continue_video_plot_audit,
    should_continue_video_qc,
    _extract_motion_prompt_from_plot
)
from core.loaders.graphs_loader import GraphsLoader
from langgraph.checkpoint.memory import MemorySaver

class TestContentCreationGraph(unittest.IsolatedAsyncioTestCase):

    def test_prepare_input(self):
        input_data = prepare_input(
            "topic: puppy, project_dir: pkm/wiki/software/toddler-tales",
            caller="main-bot",
            session_id="test_sess_1"
        )
        self.assertEqual(input_data["topic"], "puppy")
        self.assertEqual(input_data["project_dir"], "pkm/wiki/software/toddler-tales")
        self.assertTrue(input_data["manifest_path"].endswith("01_Project_Manifest.md"))
        self.assertTrue(input_data["creator_instructions_path"].endswith("02_Creator_Instructions.md"))
        self.assertTrue(input_data["qc_playbook_path"].endswith("03_QC_Playbook.md"))
        self.assertEqual(input_data["output_dir"], "pkm/wiki/software/toddler-tales/assets/puppy")
        self.assertTrue(input_data["image_path"].endswith("puppy_image.jpg"))
        self.assertTrue(input_data["video_plot_path"].endswith("puppy_video_plot.md"))
        self.assertTrue(input_data["video_path"].endswith("puppy_video.mp4"))
        self.assertTrue(input_data["copy_path"].endswith("puppy_copy.md"))
        self.assertEqual(input_data["qc_timestamps"], [1.0, 2.5, 4.0])
        self.assertFalse(input_data["video_plot_qc_passed"])
        self.assertFalse(input_data["video_qc_passed"])
        self.assertEqual(input_data["session_id"], "test_sess_1")
        self.assertIn("messages", input_data)
        self.assertEqual(len(input_data["messages"]), 1)

    def test_format_output(self):
        state = {
            "messages": [
                AIMessage(content="🎉 Final Delivery Complete")
            ]
        }
        self.assertEqual(format_output(state), "🎉 Final Delivery Complete")

        state_with_copy = {"copy_text": "Meet the cute puppy! #Stories"}
        self.assertEqual(format_output(state_with_copy), "Meet the cute puppy! #Stories")

        state_with_error = {"error_message": "Generation failed"}
        self.assertEqual(format_output(state_with_error), "Content creation failed: Generation failed")

    def test_routing_conditions(self):
        # Video plot audit routing
        self.assertEqual(should_continue_video_plot_audit({"video_plot_qc_passed": True}), "hitl_image_and_plot_approval")
        self.assertEqual(should_continue_video_plot_audit({"video_plot_qc_passed": False, "video_plot_attempts": 1, "max_video_plot_reviews": 3}), "draft_video_plot")
        self.assertEqual(should_continue_video_plot_audit({"video_plot_qc_passed": False, "video_plot_attempts": 3, "max_video_plot_reviews": 3}), "hitl_image_and_plot_approval")

        # Video QC routing
        self.assertEqual(should_continue_video_qc({"video_qc_passed": True}), "draft_and_save_copy")
        self.assertEqual(should_continue_video_qc({"video_qc_passed": False, "video_qc_attempts": 1, "max_video_reviews": 3}), "generate_visual_plate")
        self.assertEqual(should_continue_video_qc({"video_qc_passed": False, "video_qc_attempts": 3, "max_video_reviews": 3}), "draft_and_save_copy")

    def test_extract_motion_prompt(self):
        plot_md = (
            "# Video Plot: Puppy\n\n"
            "## 🎬 Motion Prompt\n"
            "> A playful golden retriever puppy running in a lush green backyard. Smooth cinematic tracking shot.\n\n"
            "## Post-Production\n"
            "1. Color grading\n"
        )
        motion_prompt = _extract_motion_prompt_from_plot(plot_md, {"topic": "puppy"})
        self.assertIn("playful golden retriever puppy", motion_prompt)
        self.assertIn("Smooth cinematic tracking shot", motion_prompt)

    def test_graphs_loader_discovery(self):
        loader = GraphsLoader()
        graph_info = loader.get_graph("content_creation")
        self.assertIsNotNone(graph_info)
        self.assertEqual(graph_info["metadata"].get("name"), "content_creation")
        self.assertIsNotNone(graph_info["create_graph"])

    async def test_graph_two_hitl_gates_and_resume(self):
        # Setup mock content-creator
        mock_creator = MagicMock()
        mock_creator.execute = AsyncMock(side_effect=[
            # 1. Image prompt (Node 1)
            "A playful golden puppy in a sunny garden, studio lighting.",
            # 2. Video plot markdown (Node 2)
            (
                "# Video Plot: Puppy\n\n"
                "## 🎬 Motion Prompt\n"
                "> A golden puppy running forward towards the camera with joyful energy. Smooth tracking shot.\n\n"
                "## Notes\n"
                "- Keep lighting consistent.\n"
            ),
            # 3. Draft caption (Node 6)
            "Meet our new little friend exploring the sunny garden! 🐶✨ #PuppyAdventures"
        ])

        # Setup mock brand-editor
        mock_editor = MagicMock()
        mock_editor.execute = AsyncMock(side_effect=[
            # 1. Audit video plot (Node 3)
            "VERDICT: APPROVED\nVideo plot satisfies all criteria in the QC playbook.",
            # 2. QC extracted frames (Node 5)
            "VERDICT: APPROVED\nAll keyframes maintain character consistency and visual fidelity.",
            # 3. Polish caption (Node 6)
            "🐶 Meet our cutest little explorer enjoying the sunshine! 🌟 What's your favorite puppy moment? Tell us below! 👇 #PuppyAdventures #StoryTime"
        ])

        def agent_dispatcher(agent_id):
            if agent_id == "content-creator":
                return mock_creator
            return mock_editor

        with patch("core.loaders.agents_loader.AgentsLoader.get_agent", side_effect=agent_dispatcher), \
             patch("graphs.content_creation.graph.generate_image") as mock_img, \
             patch("graphs.content_creation.graph.generate_animation_runway") as mock_anim, \
             patch("graphs.content_creation.graph.extract_video_frames") as mock_frames:

            mock_img.ainvoke = AsyncMock(
                return_value='<generate_image_response><payload>assets/puppy/puppy_image.jpg</payload><errors>None</errors></generate_image_response>'
            )
            mock_anim.ainvoke = AsyncMock(
                return_value='<generate_animation_runway_response><payload>assets/puppy/puppy_video.mp4</payload><errors>None</errors></generate_animation_runway_response>'
            )
            mock_frames.ainvoke = AsyncMock(
                return_value='<extract_video_frames_response><payload>assets/puppy/frames/frame_001_1_000s.jpg\nassets/puppy/frames/frame_002_2_500s.jpg\nassets/puppy/frames/frame_003_4_000s.jpg</payload><errors>None</errors></extract_video_frames_response>'
            )

            # Get current module references from sys.modules in case GraphsLoader reloaded the module
            import sys
            import graphs.content_creation.graph as current_graph_mod
            mod = sys.modules.get("graphs.content_creation.graph", current_graph_mod)
            current_create_graph = getattr(mod, "create_graph")
            current_prepare_input = getattr(mod, "prepare_input")

            # Create checkpointer
            checkpointer = MemorySaver()
            test_graph = current_create_graph(checkpointer=checkpointer)

            config = {"configurable": {"thread_id": "test_thread_generic_123"}}
            initial_state = current_prepare_input(
                "topic: puppy, project_dir: pkm/wiki/software/toddler-tales",
                session_id="test_thread_generic_123"
            )

            # -------------------------------------------------------------
            # Phase 1: Run graph until HITL Gate 1
            # -------------------------------------------------------------
            await test_graph.ainvoke(initial_state, config=config)

            state_gate1 = test_graph.get_state(config)
            self.assertEqual(state_gate1.next, ("hitl_image_and_plot_approval",))
            self.assertTrue(state_gate1.values["video_plot_qc_passed"])
            self.assertTrue(state_gate1.values["image_path"].endswith("puppy_image.jpg"))
            self.assertTrue(state_gate1.values["video_plot_path"].endswith("puppy_video_plot.md"))

            # -------------------------------------------------------------
            # Phase 2: Resume from HITL Gate 1 -> runs until HITL Gate 2
            # -------------------------------------------------------------
            await test_graph.ainvoke(None, config=config)

            state_gate2 = test_graph.get_state(config)
            self.assertEqual(state_gate2.next, ("hitl_final_package_approval",))
            self.assertTrue(state_gate2.values["video_qc_passed"])
            self.assertTrue(state_gate2.values["video_path"].endswith("puppy_video.mp4"))
            self.assertEqual(len(state_gate2.values["extracted_frames"]), 3)
            self.assertIn("final_package", state_gate2.values)
            self.assertEqual(state_gate2.values["final_package"]["topic"], "puppy")

            # -------------------------------------------------------------
            # Phase 3: Resume from HITL Gate 2 -> reaches END
            # -------------------------------------------------------------
            final_state = await test_graph.ainvoke(None, config=config)

            final_check = test_graph.get_state(config)
            self.assertEqual(final_check.next, ())
            self.assertIn("HITL GATE 2: Final Package Review & Approval", final_state["messages"][-1].content)
            self.assertIn("puppy_video.mp4", final_state["messages"][-1].content)


if __name__ == "__main__":
    unittest.main()


