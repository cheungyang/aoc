import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import tempfile
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from graphs.content_creation.nodes.ideation import generate_image_task

class TestSetupAndGenerateImageNode(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_existing_image_when_no_revision_requested(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            existing_image_path = os.path.join(output_dir, "cat_image.jpg")
            with open(existing_image_path, "wb") as f:
                f.write(b"EXISTING_CAT_IMAGE_BYTES")

            state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": output_dir,
            }

            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_gen:
                mock_gen.ainvoke = AsyncMock()
                result = await generate_image_task(state)

                mock_gen.ainvoke.assert_not_called()
                self.assertEqual(result["image_path"], existing_image_path)
                self.assertFalse(os.path.exists(os.path.join(output_dir, "cat_image_v2.jpg")))

    async def test_generates_v2_when_gate1_requests_revise_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = os.path.join(temp_dir, "cat")
            os.makedirs(output_dir, exist_ok=True)
            existing_image_path = os.path.join(output_dir, "cat_image.jpg")
            with open(existing_image_path, "wb") as f:
                f.write(b"EXISTING_CAT_IMAGE_BYTES")

            state = {
                "topic": "cat",
                "project_dir": temp_dir,
                "output_dir": output_dir,
                "gate1_decision": "revise_image",
                "latest_human_feedback": "make the cat orange"
            }

            target_path = os.path.join(output_dir, "cat_image_v2.jpg")
            with patch("graphs.content_creation.nodes.ideation.generate_image.generate_image") as mock_gen:
                mock_gen.ainvoke = AsyncMock(return_value=f"<payload>{target_path}</payload>")

                result = await generate_image_task(state)

                mock_gen.ainvoke.assert_called_once()
                self.assertEqual(result["image_path"], target_path)

if __name__ == "__main__":
    unittest.main()
