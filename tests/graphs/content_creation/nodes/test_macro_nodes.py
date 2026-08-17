import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import tempfile
import os
from langchain_core.messages import AIMessage

from graphs.content_creation.nodes.ingestion.ingest_audio_node import ingest_audio_node
from graphs.content_creation.nodes.ideation.ideate_package_node import ideate_package_node
from graphs.content_creation.nodes.production.produce_deliverables_node import produce_deliverables_node

class TestMacroNodes(unittest.IsolatedAsyncioTestCase):

    async def test_ingest_audio_node_finds_local_audio(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio_path = os.path.join(temp_dir, "Cat.m4a")
            with open(audio_path, "wb") as f:
                f.write(b"fake audio data")

            state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": temp_dir,
                "audio": audio_path
            }
            res = await ingest_audio_node(state)
            self.assertEqual(res["source_audio_path"], audio_path)

    async def test_ingest_audio_node_asks_when_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": temp_dir
            }
            res = await ingest_audio_node(state)
            self.assertEqual(res["output_dir"], temp_dir)

    async def test_ideate_package_node(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            
            with patch("graphs.content_creation.nodes.ideation.ideate_package_node.generate_image_task", new_callable=AsyncMock) as mock_img, \
                 patch("graphs.content_creation.nodes.ideation.ideate_package_node.draft_plot_task", new_callable=AsyncMock) as mock_plot, \
                 patch("graphs.content_creation.nodes.ideation.ideate_package_node.audit_plot_task", new_callable=AsyncMock) as mock_audit:
                 
                mock_img.return_value = {"image_path": os.path.join(output_dir, "cat_image.jpg")}
                mock_plot.return_value = {"video_plot_path": os.path.join(output_dir, "cat_video_plot.md"), "overlay_text": "CAT"}
                mock_audit.return_value = {"video_plot_qc_passed": True}

                state = {
                    "topic": "cat",
                    "project_dir": temp_dir,
                    "output_dir": output_dir
                }
                res = await ideate_package_node(state)
                self.assertEqual(res["image_path"], os.path.join(output_dir, "cat_image.jpg"))
                self.assertEqual(res["video_plot_path"], os.path.join(output_dir, "cat_video_plot.md"))
                self.assertTrue(res["video_plot_qc_passed"])
                self.assertIn("messages", res)

    async def test_produce_deliverables_node(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            
            with patch("graphs.content_creation.nodes.production.produce_deliverables_node.render_plate_task", new_callable=AsyncMock) as mock_plate, \
                 patch("graphs.content_creation.nodes.production.produce_deliverables_node.remix_video_task", new_callable=AsyncMock) as mock_remix, \
                 patch("graphs.content_creation.nodes.production.produce_deliverables_node.verify_video_task", new_callable=AsyncMock) as mock_verify, \
                 patch("graphs.content_creation.nodes.production.produce_deliverables_node.draft_copy_task", new_callable=AsyncMock) as mock_copy:
                 
                mock_plate.return_value = {"raw_video_path": os.path.join(output_dir, "cat_raw.mp4")}
                mock_remix.return_value = {"remixed_video_path": os.path.join(output_dir, "cat_video.mp4")}
                mock_verify.return_value = {
                    "extracted_frames_path": ["frame1.png"],
                    "video_qc_passed": True,
                    "video_qc_attempts": 1
                }
                mock_copy.return_value = {"copy_path": os.path.join(output_dir, "cat_copy.md")}

                state = {
                    "topic": "cat",
                    "project_dir": temp_dir,
                    "output_dir": output_dir,
                    "image_path": os.path.join(output_dir, "cat_image.jpg"),
                    "video_plot_path": os.path.join(output_dir, "cat_video_plot.md")
                }
                res = await produce_deliverables_node(state)
                self.assertEqual(res["raw_video_path"], os.path.join(output_dir, "cat_raw.mp4"))
                self.assertEqual(res["remixed_video_path"], os.path.join(output_dir, "cat_video.mp4"))
                self.assertEqual(res["copy_path"], os.path.join(output_dir, "cat_copy.md"))
                self.assertEqual(res["extracted_frames_path"], ["frame1.png"])
                self.assertTrue(res["video_qc_passed"])
                self.assertIn("messages", res)

if __name__ == "__main__":
    unittest.main()
