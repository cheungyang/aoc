import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import tempfile
import os
import json
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

            expected_v2_path = os.path.join(output_dir, "cat_video_plot_v2.md")
            mock_response = (
                f"<payload>\n"
                f"<status>success</status>\n"
                f"<error></error>\n"
                f"<title>Cat Video Plot V2</title>\n"
                f"<video_plot_path>{expected_v2_path}</video_plot_path>\n"
                f"<motion_prompt>Fast cat running</motion_prompt>\n"
                f"<overlay_text>CAT</overlay_text>\n"
                f"</payload>"
            )

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)

                result = await draft_plot_task(state)

                self.assertEqual(result["video_plot_path"], expected_v2_path)
                self.assertTrue(os.path.exists(expected_v2_path))
                with open(result["video_plot_path"], "r") as f:
                    content = f.read()
                    self.assertIn("Fast cat running", content)
                    self.assertIn("CAT", content)

    async def test_draft_plot_parses_reinforced_xml_path_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            plot_md_path = os.path.join(output_dir, "cat_video_plot.md")

            state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": output_dir
            }

            mock_response = (
                f"<payload>\n"
                f"<status>success</status>\n"
                f"<error></error>\n"
                f"<title>Playful Cat</title>\n"
                f"<video_plot_path>{plot_md_path}</video_plot_path>\n"
                f"<motion_prompt>Cat jumping playful kitten paws motion</motion_prompt>\n"
                f"<overlay_text>貓貓</overlay_text>\n"
                f"</payload>"
            )

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)

                result = await draft_plot_task(state)

                self.assertEqual(result["overlay_text"], "貓貓")
                self.assertEqual(result["video_plot_path"], plot_md_path)
                json_file = plot_md_path.replace(".md", ".json")
                self.assertTrue(os.path.exists(json_file))
                with open(json_file, "r") as f:
                    data = json.load(f)
                    self.assertEqual(data["motion_prompt"], "Cat jumping playful kitten paws motion")
                    self.assertEqual(data["overlay_text"], "貓貓")
                    self.assertEqual(data["title"], "Playful Cat")

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

            plot_md_path = os.path.join(output_dir, "cat_video_plot.md")
            mock_response = (
                f"<payload>\n"
                f"<status>success</status>\n"
                f"<error></error>\n"
                f"<title>Cat</title>\n"
                f"<video_plot_path>{plot_md_path}</video_plot_path>\n"
                f"<motion_prompt>Kitten paws motion</motion_prompt>\n"
                f"<overlay_text>CAT</overlay_text>\n"
                f"</payload>"
            )

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)

                result = await draft_plot_task(state)

                mock_agent_call.ainvoke.assert_called_once()
                call_args = mock_agent_call.ainvoke.call_args[0][0]
                self.assertEqual(call_args["agent_id"], "graph-worker")
                call_prompt = call_args["prompt"]
                self.assertIn("<playbook>", call_prompt)
                self.assertIn("<current_state>", call_prompt)
                self.assertIn("<assigned_task>", call_prompt)
                self.assertIn("<video_plot_path>{video_plot_path}</video_plot_path>", call_prompt)
                self.assertIn("CREATOR_MOTION_STANDARDS", call_prompt)
                self.assertIn("3D_CHARACTER_TRAITS", call_prompt)
                self.assertIn("Toddler girl should perform playful kitten paws.", call_prompt)

    async def test_draft_plot_parses_json_payload_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)

            state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": output_dir
            }

            json_payload = json.dumps({
                "title": "Cute Kitten",
                "motion_prompt": "Kitten wiggling tail",
                "overlay_text": "貓貓",
                "markdown_content": "# Cat Plot\nCute motion\nOverlay Text: 貓貓"
            })
            mock_response = f"<payload>{json_payload}</payload>"

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)

                result = await draft_plot_task(state)

                self.assertEqual(result["overlay_text"], "貓貓")
                json_file = result["video_plot_path"].replace(".md", ".json")
                self.assertTrue(os.path.exists(json_file))
                with open(json_file, "r") as f:
                    data = json.load(f)
                    self.assertEqual(data["motion_prompt"], "Kitten wiggling tail")

    async def test_draft_plot_handles_agent_call_exception_gracefully(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)

            state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": output_dir
            }

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(side_effect=RuntimeError("Agent call timeout"))

                result = await draft_plot_task(state)

                self.assertIn("video_plot_path", result)
                self.assertEqual(result["overlay_text"], "")

    async def test_generates_v2_when_plot_feedback_provided_even_if_gate1_decision_was_approved(self):
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
                "latest_human_feedback": "Change the video plot motion: add rapid zoom and camera pan.",
                "creator_instructions_path": os.path.join(temp_dir, "02_Creator_Instructions.md")
            }

            with open(state["creator_instructions_path"], "w") as f:
                f.write("Instructions")

            expected_v2 = os.path.join(output_dir, "cat_video_plot_v2.md")
            mock_response = f"<payload><video_plot_path>{expected_v2}</video_plot_path><motion_prompt>rapid zoom and pan</motion_prompt><overlay_text>CAT</overlay_text></payload>"

            with patch("tools.agent_call.agent_call") as mock_agent_call:
                mock_agent_call.ainvoke = AsyncMock(return_value=mock_response)

                result = await draft_plot_task(state)

                self.assertEqual(result["video_plot_path"], expected_v2)
                self.assertTrue(os.path.exists(expected_v2))
                with open(result["video_plot_path"], "r") as f:
                    self.assertIn("rapid zoom and pan", f.read())

    async def test_reuses_existing_plot_when_image_specific_feedback_provided(self):
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
                "gate1_decision": "revise_image",
                "video_plot_qc_passed": True,
                "latest_human_feedback": "Change character costume to blue astronaut onesie.",
                "creator_instructions_path": os.path.join(temp_dir, "02_Creator_Instructions.md")
            }

            result = await draft_plot_task(state)
            self.assertEqual(result["video_plot_path"], existing_plot_path)

if __name__ == "__main__":
    unittest.main()
