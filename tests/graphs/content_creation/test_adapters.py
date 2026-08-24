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
from graphs.content_creation.utils.paths import normalize_path

class TestContentCreationStateAndAdapters(unittest.TestCase):

    def test_prepare_input_with_project_and_output_path(self):
        input_data = prepare_input(
            "topic: Puppy, project_path: pkm/wiki/software/toddler-tales, output_path: pkm/wiki/software/toddler-tales/words/puppy",
            caller="main-bot",
            session_id="test_sess_1"
        )
        self.assertEqual(input_data["topic"], "puppy")
        self.assertEqual(input_data["project_path"], normalize_path("pkm/wiki/software/toddler-tales"))
        self.assertEqual(input_data["output_path"], normalize_path("pkm/wiki/software/toddler-tales/words/puppy"))
        self.assertTrue(input_data["manifest_path"].endswith("01_Project_Manifest.md"))
        self.assertTrue(input_data["creator_instructions_path"].endswith("02_Creator_Instructions.md"))
        self.assertTrue(input_data["qc_playbook_path"].endswith("03_QC_Playbook.md"))
        self.assertTrue(input_data["image_path"].endswith("puppy_image.jpg"))
        self.assertTrue(input_data["video_plot_path"].endswith("puppy_video_plot.md"))
        self.assertTrue(input_data["raw_video_path"].endswith("puppy_raw_video.mp4"))
        self.assertTrue(input_data["remixed_video_path"].endswith("puppy_video.mp4"))
        self.assertTrue(input_data["copy_path"].endswith("puppy_copy.md"))
        self.assertEqual(input_data["thread_id"], "test_sess_1")
        self.assertFalse(input_data["video_plot_qc_passed"])
        self.assertFalse(input_data["video_qc_passed"])
        self.assertEqual(input_data["error_message"], "")
        self.assertIn("messages", input_data)
        self.assertEqual(len(input_data["messages"]), 1)

    def test_prepare_input_missing_output_path_fails(self):
        # When output_path is missing, initialization halts with an error
        input_data = prepare_input(
            "topic: Puppy, project_path: pkm/wiki/software/toddler-tales",
            caller="main-bot",
            session_id="test_sess_missing_out"
        )
        self.assertTrue(len(input_data["error_message"]) > 0)
        self.assertIn("Missing required project/output path", input_data["error_message"])
        self.assertEqual(input_data["project_path"], "")
        self.assertEqual(input_data["output_path"], "")

    def test_prepare_input_missing_project_path_fails(self):
        # When project_path is missing, initialization halts with an error
        input_data = prepare_input(
            "topic: Puppy, output_path: pkm/wiki/software/toddler-tales/words/puppy",
            caller="main-bot",
            session_id="test_sess_missing_proj"
        )
        self.assertTrue(len(input_data["error_message"]) > 0)
        self.assertIn("Missing required project/output path", input_data["error_message"])

    def test_prepare_input_missing_both_paths_fails(self):
        input_data = prepare_input("create video for puppy", session_id="test_sess_no_paths")
        self.assertTrue(len(input_data["error_message"]) > 0)
        self.assertIn("Missing required project/output path", input_data["error_message"])
        self.assertEqual(input_data["project_path"], "")
        self.assertEqual(input_data["output_path"], "")
        self.assertEqual(input_data["manifest_path"], "")

    def test_prepare_input_does_not_treat_feedback_as_topic(self):
        feedback = "project_path: pkm/wiki/software/toddler-tales, output_path: pkm/wiki/software/toddler-tales/words/fish, i am looking for ayla in a full fish mascot outfit, instead of wearing a jacket with fish icons."
        input_data = prepare_input(feedback, session_id="test_sess_fb")
        self.assertNotEqual(input_data["topic"], feedback.lower())
        self.assertEqual(input_data["topic"], "scene")

    def test_format_output(self):
        state = {
            "messages": [
                AIMessage(content="🎉 Final Delivery Complete")
            ]
        }
        self.assertEqual(format_output(state), "🎉 Final Delivery Complete")

        state_with_clarify = {"clarification_question": "Please specify image or plot"}
        self.assertEqual(format_output(state_with_clarify), "Please specify image or plot")

        state_with_error = {"error_message": "Generation failed"}
        self.assertEqual(format_output(state_with_error), "Content creation failed: Generation failed")

    def test_state_schema_propagates_project_and_output_paths(self):
        input_data = prepare_input(
            "topic: puppy, project_path: pkm/wiki/software/ayla-first-words, output_path: pkm/wiki/software/ayla-first-words/words/puppy",
            caller="main-bot",
            session_id="test_sess_propagate"
        )
        self.assertEqual(input_data["project_path"], normalize_path("pkm/wiki/software/ayla-first-words"))
        self.assertEqual(input_data["output_path"], normalize_path("pkm/wiki/software/ayla-first-words/words/puppy"))
        self.assertTrue(input_data["manifest_path"].startswith(normalize_path("pkm/wiki/software/ayla-first-words")))
        self.assertTrue(input_data["image_path"].startswith(normalize_path("pkm/wiki/software/ayla-first-words/words/puppy")))

    def test_paths_strictly_under_project_or_output_path(self):
        project_path = "projects/storybooks/vol1"
        output_path = "projects/storybooks/vol1/output/chapter1"
        input_data = prepare_input(
            f"topic: adventure, project_path: {project_path}, output_path: {output_path}",
            session_id="test_sess_containment"
        )
        norm_proj = normalize_path(project_path)
        norm_out = normalize_path(output_path)
        
        # Instruction docs must be under project_path
        self.assertTrue(input_data["manifest_path"].startswith(norm_proj))
        self.assertTrue(input_data["creator_instructions_path"].startswith(norm_proj))
        self.assertTrue(input_data["qc_playbook_path"].startswith(norm_proj))

        # Asset and execution files must be under output_path
        self.assertTrue(input_data["execution_log_path"].startswith(norm_out))
        self.assertTrue(input_data["image_path"].startswith(norm_out))
        self.assertTrue(input_data["video_plot_path"].startswith(norm_out))
        self.assertTrue(input_data["remixed_video_path"].startswith(norm_out))
        self.assertTrue(input_data["copy_path"].startswith(norm_out))


if __name__ == "__main__":
    unittest.main()
