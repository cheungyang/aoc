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

            with patch("langchain_google_genai.ChatGoogleGenerativeAI") as MockLLM:
                result = await draft_plot_task(state)

                MockLLM.assert_not_called()
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

            mock_plot = VideoPlot(
                title="Cat Video Plot",
                source_image=os.path.join(output_dir, "cat_image.jpg"),
                source_audio=os.path.join(output_dir, "cat_audio.m4a"),
                motion_prompt="Fast cat running",
                overlay_text="CAT",
                markdown_content="V2 plot content with faster motion"
            )

            with patch("core.loaders.agents_loader.AgentsLoader") as MockLoader, \
                 patch("langchain_google_genai.ChatGoogleGenerativeAI") as MockLLM:
                mock_llm_instance = MagicMock()
                mock_structured = AsyncMock()
                mock_structured.ainvoke.return_value = mock_plot
                mock_llm_instance.with_structured_output.return_value = mock_structured
                MockLLM.return_value = mock_llm_instance

                result = await draft_plot_task(state)

                expected_v2 = os.path.join(output_dir, "cat_video_plot_v2.md")
                self.assertEqual(result["video_plot_path"], expected_v2)
                self.assertTrue(os.path.exists(expected_v2))
                with open(result["video_plot_path"], "r") as f:
                    self.assertEqual(f.read(), "V2 plot content with faster motion")

if __name__ == "__main__":
    unittest.main()
