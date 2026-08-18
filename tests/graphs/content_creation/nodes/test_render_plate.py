import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import tempfile
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

import graphs.content_creation.nodes.production.render_plate as render_plate_module
from graphs.content_creation.nodes.production import render_plate_task

class TestRenderPlate(unittest.IsolatedAsyncioTestCase):

    async def test_render_plate_calls_veo3_with_prompt_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            image_path = os.path.join(output_dir, "cat_image.jpg")
            with open(image_path, "wb") as f:
                f.write(b"IMAGE_BYTES")

            mock_veo = AsyncMock()
            target_video = os.path.join(output_dir, "cat_raw_video.mp4")
            mock_veo.ainvoke.return_value = f"<payload>{target_video}</payload><errors>None</errors>"

            with patch.object(render_plate_module, "generate_animation_veo3", mock_veo):
                state = {
                    "topic": "cat",
                    "project_dir": temp_dir,
                    "output_dir": output_dir,
                    "image_path": image_path
                }
                res = await render_plate_task(state)

                mock_veo.ainvoke.assert_called_once()
                call_kwargs = mock_veo.ainvoke.call_args[0][0]
                self.assertIn("prompt_text", call_kwargs)
                self.assertEqual(call_kwargs["image_path"], image_path)
                self.assertEqual(call_kwargs["duration"], 6)
                self.assertEqual(call_kwargs["agent_id"], "content-creator")
                self.assertEqual(res["raw_video_path"], target_video)

    async def test_render_plate_reuses_existing_when_qc_passed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            existing_plate = os.path.join(output_dir, "cat_raw_video.mp4")
            with open(existing_plate, "wb") as f:
                f.write(b"EXISTING_VIDEO_BYTES")

            mock_veo = AsyncMock()
            with patch.object(render_plate_module, "generate_animation_veo3", mock_veo):
                state = {
                    "topic": "cat",
                    "project_dir": temp_dir,
                    "output_dir": output_dir,
                    "video_qc_passed": True
                }
                res = await render_plate_task(state)
                mock_veo.ainvoke.assert_not_called()
                self.assertEqual(res["raw_video_path"], existing_plate)

if __name__ == "__main__":
    unittest.main()
