import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import tempfile
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from graphs.content_creation.nodes.ideation import draft_plot_task
from graphs.content_creation.schemas import VideoPlot

class TestDraftVideoPlotNode(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_existing_plot_when_qc_passed_and_no_revision_requested(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            existing_plot_path = os.path.join(output_dir, "cat_video_plot.md")
            with open(existing_plot_path, "w") as f:
                f.write("Existing approved video plot motion")

            state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": output_dir,
                "video_plot_qc_passed": True
            }

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                result = await draft_plot_task(state)

                mock_agent_call.ainvoke.assert_not_called()
                self.assertEqual(result["video_plot_path"], existing_plot_path)
                with open(result["video_plot_path"], "r") as f:
                    self.assertEqual(f.read(), "Existing approved video plot motion")
                self.assertFalse(os.path.exists(os.path.join(output_dir, "cat_video_plot_v2.md")))

    async def test_generates_v2_when_feedback_provided(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            existing_plot_path = os.path.join(output_dir, "cat_video_plot.md")
            with open(existing_plot_path, "w") as f:
                f.write("Initial plot")

            state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": output_dir,
                "video_plot_feedback": "Motion needs to be faster.",
                "creator_instructions_path": os.path.join(temp_dir, "02_Creator_Instructions.md")
            }

            with open(state["creator_instructions_path"], "w") as f:
                f.write("Instructions")

            mock_response = "<payload>V2 plot content with faster motion\nOverlay Text: CAT</payload>"

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)

                result = await draft_plot_task(state)

                expected_v2 = os.path.join(output_dir, "cat_video_plot_v2.md")
                self.assertEqual(result["video_plot_path"], expected_v2)
                self.assertTrue(os.path.exists(expected_v2))
                with open(result["video_plot_path"], "r") as f:
                    self.assertEqual(f.read(), "V2 plot content with faster motion\nOverlay Text: CAT")

    async def test_draft_plot_dynamically_loads_instructions_and_feedback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            char_dir = os.path.join(temp_dir, "character")
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(char_dir, exist_ok=True)

            instr_path = os.path.join(temp_dir, "02_Creator_Instructions.md")
            with open(instr_path, "w") as f:
                f.write("CREATOR_MOTION_STANDARDS")

            sheet_path = os.path.join(char_dir, "01_Character_Sheet_3D.md")
            with open(sheet_path, "w") as f:
                f.write("---\nstyle: 3D\n---\n3D_CHARACTER_TRAITS")

            state = {
                "topic": "cat",
                "style": "3D",
                "project_dir": temp_dir,
                "output_dir": output_dir,
                "creator_instructions_path": instr_path,
                "latest_human_feedback": "Toddler girl should perform playful kitten paws."
            }

            mock_response = "<payload>Playful kitten paws plot content\nOverlay Text: CAT</payload>"

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)

                result = await draft_plot_task(state)

                mock_agent_call.ainvoke.assert_called_once()
                call_args = mock_agent_call.ainvoke.call_args[0][0]
                self.assertEqual(call_args["agent_id"], "content-creator")
                call_prompt = call_args["prompt"]
                self.assertIn("CREATOR_MOTION_STANDARDS", call_prompt)
                self.assertIn("3D_CHARACTER_TRAITS", call_prompt)
                self.assertIn("Toddler girl should perform playful kitten paws.", call_prompt)

    async def test_generates_v2_when_feedback_provided_even_if_gate1_decision_was_approved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            existing_plot_path = os.path.join(output_dir, "cat_video_plot.md")
            with open(existing_plot_path, "w") as f:
                f.write("Initial plot")

            state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": output_dir,
                "gate1_decision": "approved",
                "latest_human_feedback": "Use reference image and character/ayla_3d.jpg. have ayla wear a cat costume, in the post of pretending like a cat crawling on the floor. Do not include any actual cats in the image.",
                "creator_instructions_path": os.path.join(temp_dir, "02_Creator_Instructions.md")
            }

            with open(state["creator_instructions_path"], "w") as f:
                f.write("Instructions")

            mock_response = "<payload>V2 plot content with Ayla in cat costume crawling\nOverlay Text: CAT</payload>"

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)

                result = await draft_plot_task(state)

                expected_v2 = os.path.join(output_dir, "cat_video_plot_v2.md")
                self.assertEqual(result["video_plot_path"], expected_v2)
                self.assertTrue(os.path.exists(expected_v2))
                with open(result["video_plot_path"], "r") as f:
                    self.assertEqual(f.read(), "V2 plot content with Ayla in cat costume crawling\nOverlay Text: CAT")

if __name__ == "__main__":
    unittest.main()
