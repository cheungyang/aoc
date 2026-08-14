import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys
import base64

# Inject root directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.generate_animation_runway import generate_animation_runway
from core.util import format_tool_response

class TestGenerateAnimationRunwayTool(unittest.IsolatedAsyncioTestCase):

    @patch.dict(os.environ, {}, clear=True)
    async def test_missing_api_key(self):
        result = await generate_animation_runway.ainvoke({
            "prompt_text": "gentle camera push in",
            "image_path": "scene.png",
            "output_path": "video.mp4"
        })
        self.assertIn("Error: RUNWAYML_API_SECRET environment variable not set", result)

    @patch.dict(os.environ, {"RUNWAYML_API_SECRET": "test_runway_key"})
    async def test_empty_prompt_text(self):
        result = await generate_animation_runway.ainvoke({
            "prompt_text": "",
            "image_path": "scene.png",
            "output_path": "video.mp4"
        })
        self.assertIn("Error: prompt_text cannot be empty", result)

    @patch.dict(os.environ, {"RUNWAYML_API_SECRET": "test_runway_key"})
    @patch("os.path.exists", return_value=False)
    async def test_nonexistent_image_file(self, mock_exists):
        result = await generate_animation_runway.ainvoke({
            "prompt_text": "gentle camera push in",
            "image_path": "nonexistent.png",
            "output_path": "video.mp4"
        })
        self.assertIn("Error: Image file not found at 'nonexistent.png'", result)

    @patch.dict(os.environ, {"RUNWAYML_API_SECRET": "test_runway_key"})
    @patch("os.path.exists", return_value=True)
    async def test_empty_output_path(self, mock_exists):
        result = await generate_animation_runway.ainvoke({
            "prompt_text": "gentle camera push in",
            "image_path": "scene.png",
            "output_path": ""
        })
        self.assertIn("Error: output_path cannot be empty", result)

    @patch.dict(os.environ, {"RUNWAYML_API_SECRET": "test_runway_key"})
    @patch("os.path.exists", return_value=True)
    async def test_successful_video_generation(self, mock_exists):
        fake_img_bytes = b"fake_png_data"
        fake_video_bytes = b"fake_mp4_video_data"

        # Mock httpx responses
        mock_create_res = MagicMock()
        mock_create_res.status_code = 200
        mock_create_res.json.return_value = {"id": "task_12345"}

        mock_poll_res = MagicMock()
        mock_poll_res.status_code = 200
        mock_poll_res.json.return_value = {
            "status": "SUCCEEDED",
            "output": ["https://runway.mock/video.mp4"]
        }

        mock_video_res = MagicMock()
        mock_video_res.status_code = 200
        mock_video_res.content = fake_video_bytes
        mock_video_res.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_create_res)
        mock_client.get = AsyncMock(side_effect=[mock_poll_res, mock_video_res])
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("builtins.open", unittest.mock.mock_open(read_data=fake_img_bytes)) as mock_file:
                with patch("os.makedirs"):
                    with patch("os.path.abspath", return_value="/mock/path/video.mp4"):
                        result = await generate_animation_runway.ainvoke({
                            "prompt_text": "subtle character smile, slow push in",
                            "image_path": "scene.png",
                            "output_path": "video.mp4",
                            "poll_interval": 0.01,
                            "max_wait_seconds": 5.0
                        })

        expected = format_tool_response("generate_animation_runway", payload="/mock/path/video.mp4", errors="None")
        self.assertEqual(result, expected)

    @patch.dict(os.environ, {"RUNWAYML_API_SECRET": "test_runway_key"})
    @patch("os.path.exists", return_value=True)
    async def test_task_failed(self, mock_exists):
        fake_img_bytes = b"fake_png_data"

        mock_create_res = MagicMock()
        mock_create_res.status_code = 200
        mock_create_res.json.return_value = {"id": "task_fail_123"}

        mock_poll_res = MagicMock()
        mock_poll_res.status_code = 200
        mock_poll_res.json.return_value = {
            "status": "FAILED",
            "failure": "Violates motion bounds"
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_create_res)
        mock_client.get = AsyncMock(return_value=mock_poll_res)
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch("builtins.open", unittest.mock.mock_open(read_data=fake_img_bytes)):
                result = await generate_animation_runway.ainvoke({
                    "prompt_text": "extreme rapid motion",
                    "image_path": "scene.png",
                    "output_path": "video.mp4",
                    "poll_interval": 0.01,
                    "max_wait_seconds": 1.0
                })

        self.assertIn("Runway video generation failed: Violates motion bounds", result)

if __name__ == "__main__":
    unittest.main()
