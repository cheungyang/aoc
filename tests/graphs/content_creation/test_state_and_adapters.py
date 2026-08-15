import unittest
import os
import sys
from langchain_core.messages import AIMessage

# Inject root directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from graphs.content_creation.graph import (
    prepare_input,
    format_output
)
from graphs.content_creation.state import _extract_motion_prompt_from_plot
from core.loaders.graphs_loader import GraphsLoader

class TestContentCreationStateAndAdapters(unittest.TestCase):

    def test_prepare_input_with_project_dir(self):
        input_data = prepare_input(
            "topic: Puppy, project_dir: pkm/wiki/software/toddler-tales",
            caller="main-bot",
            session_id="test_sess_1"
        )
        self.assertEqual(input_data["topic"], "puppy")
        self.assertEqual(input_data["project_dir"], "pkm/wiki/software/toddler-tales")
        self.assertTrue(input_data["manifest_path"].endswith("01_Project_Manifest.md"))
        self.assertTrue(input_data["creator_instructions_path"].endswith("02_Creator_Instructions.md"))
        self.assertTrue(input_data["qc_playbook_path"].endswith("03_QC_Playbook.md"))
        self.assertEqual(input_data["output_dir"], "pkm/wiki/software/toddler-tales/puppy")
        self.assertTrue(input_data["image_path"].endswith("puppy_image.jpg"))
        self.assertTrue(input_data["video_plot_path"].endswith("puppy_video_plot.md"))
        self.assertTrue(input_data["video_path"].endswith("puppy_video.mp4"))
        self.assertTrue(input_data["copy_path"].endswith("puppy_copy.md"))
        self.assertEqual(input_data["image_version"], 1)
        self.assertEqual(input_data["video_plot_version"], 1)
        self.assertEqual(input_data["thread_id"], "test_sess_1")
        self.assertEqual(input_data["qc_timestamps"], [1.0, 2.5, 4.0])
        self.assertFalse(input_data["video_plot_qc_passed"])
        self.assertFalse(input_data["video_qc_passed"])
        self.assertEqual(input_data["error_message"], "")
        self.assertIn("messages", input_data)
        self.assertEqual(len(input_data["messages"]), 1)

    def test_prepare_input_with_explicit_output_dir(self):
        input_data = prepare_input(
            "topic: Puppy, project_dir: pkm/wiki/software/toddler-tales, output_dir: custom/output/path/puppy",
            caller="main-bot",
            session_id="test_sess_outdir"
        )
        self.assertEqual(input_data["output_dir"], "custom/output/path/puppy")
        self.assertTrue(input_data["image_path"].startswith("custom/output/path/puppy"))

    def test_prepare_input_missing_project_and_output_dir(self):
        # When neither project_dir nor output_dir is provided, initialization halts with an error requesting paths
        input_data = prepare_input("create video for puppy", session_id="test_sess_no_paths")
        self.assertTrue(len(input_data["error_message"]) > 0)
        self.assertIn("Missing required project/output path", input_data["error_message"])
        self.assertEqual(input_data["project_dir"], "")
        self.assertEqual(input_data["output_dir"], "")
        self.assertEqual(input_data["manifest_path"], "")

    def test_prepare_input_does_not_treat_feedback_as_topic(self):
        # Conversational revision feedback should not become a topic name
        feedback = "project_dir: pkm/wiki/software/toddler-tales, i am looking for ayla in a full fish mascot outfit, instead of wearing a jacket with fish icons."
        input_data = prepare_input(feedback, session_id="test_sess_fb")
        self.assertNotEqual(input_data["topic"], feedback.lower())
        self.assertEqual(input_data["topic"], "scene")
        self.assertFalse("mascot outfit" in input_data["output_dir"])

    def test_format_output(self):
        state = {
            "messages": [
                AIMessage(content="🎉 Final Delivery Complete")
            ]
        }
        self.assertEqual(format_output(state), "🎉 Final Delivery Complete")

        state_gate1_v2 = {
            "topic": "fish",
            "output_dir": "pkm/wiki/software/ayla-first-words/words/fish",
            "image_version": 2,
            "video_plot_version": 2,
            "image_path": "pkm/wiki/software/ayla-first-words/words/fish/fish_image.jpg",
            "video_plot_path": "pkm/wiki/software/ayla-first-words/words/fish/fish_video_plot.md",
            "video_plot_content": "## Motion Prompt\n> Ayla in mascot suit.",
            "video_plot_qc_passed": True
        }
        out_g1 = format_output(state_gate1_v2)
        self.assertIn("- **Base Image (v2)**: `pkm/wiki/software/ayla-first-words/words/fish/fish_image_v2.jpg`", out_g1)
        self.assertIn("- **Approved Video Plot (v2)**: `pkm/wiki/software/ayla-first-words/words/fish/fish_video_plot_v2.md`", out_g1)
        self.assertIn("<image path=\"pkm/wiki/software/ayla-first-words/words/fish/fish_image_v2.jpg\"/>", out_g1)

        state_with_copy = {
            "topic": "fish",
            "output_dir": "pkm/wiki/software/ayla-first-words/words/fish",
            "copy_version": 2,
            "copy_text": "Meet the cute puppy! #Stories"
        }
        out_g2 = format_output(state_with_copy)
        self.assertIn("- **Publication Copy File (v2)**: `pkm/wiki/software/ayla-first-words/words/fish/fish_copy_v2.md`", out_g2)
        self.assertIn("Meet the cute puppy! #Stories", out_g2)

        state_with_clarify = {"clarification_question": "Please specify image or plot"}
        self.assertEqual(format_output(state_with_clarify), "Please specify image or plot")

        state_with_error = {"error_message": "Generation failed"}
        self.assertEqual(format_output(state_with_error), "Content creation failed: Generation failed")

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


    def test_state_schema_propagates_project_and_output_dirs(self):
        input_data = prepare_input(
            "topic: puppy, project_dir: pkm/wiki/software/ayla-first-words, output_dir: pkm/wiki/software/ayla-first-words/words/puppy",
            caller="main-bot",
            session_id="test_sess_propagate"
        )
        self.assertEqual(input_data["project_dir"], "pkm/wiki/software/ayla-first-words")
        self.assertEqual(input_data["output_dir"], "pkm/wiki/software/ayla-first-words/words/puppy")
        self.assertTrue(input_data["manifest_path"].startswith("pkm/wiki/software/ayla-first-words"))
        self.assertTrue(input_data["image_path"].startswith("pkm/wiki/software/ayla-first-words/words/puppy"))

    def test_paths_strictly_under_project_or_output_dir(self):
        project_dir = "projects/storybooks/vol1"
        output_dir = "projects/storybooks/vol1/output/chapter1"
        input_data = prepare_input(
            f"topic: adventure, project_dir: {project_dir}, output_dir: {output_dir}",
            session_id="test_sess_containment"
        )
        
        # Instruction docs must be under project_dir
        self.assertTrue(input_data["manifest_path"].startswith(project_dir))
        self.assertTrue(input_data["creator_instructions_path"].startswith(project_dir))
        self.assertTrue(input_data["qc_playbook_path"].startswith(project_dir))

        # Asset and execution files must be under output_dir
        self.assertTrue(input_data["execution_log_path"].startswith(output_dir))
        self.assertTrue(input_data["image_path"].startswith(output_dir))
        self.assertTrue(input_data["video_plot_path"].startswith(output_dir))
        self.assertTrue(input_data["video_path"].startswith(output_dir))
        self.assertTrue(input_data["copy_path"].startswith(output_dir))

    def test_custom_file_args_coerced_under_project_and_output_dirs(self):
        project_dir = "projects/storybooks/vol2"
        input_data = prepare_input(
            "topic: tiger, project_dir: projects/storybooks/vol2",
            manifest_path="custom_manifest.md",
            creator_instructions_path="custom_instructions.md",
            qc_playbook_path="custom_qc.md",
            image_path="custom_art.png",
            session_id="test_sess_coerced"
        )
        expected_output_dir = "projects/storybooks/vol2/tiger"
        self.assertEqual(input_data["output_dir"], expected_output_dir)

        # Instruction docs are coerced under project_dir
        self.assertEqual(input_data["manifest_path"], f"{project_dir}/custom_manifest.md")
        self.assertEqual(input_data["creator_instructions_path"], f"{project_dir}/custom_instructions.md")
        self.assertEqual(input_data["qc_playbook_path"], f"{project_dir}/custom_qc.md")

        # Asset path is coerced under output_dir
        self.assertEqual(input_data["image_path"], f"{expected_output_dir}/custom_art.png")


if __name__ == "__main__":
    unittest.main()
