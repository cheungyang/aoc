import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

# Inject root directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.generate_animation_veo3 import generate_animation_veo3
from core.util import format_tool_response

class TestGenerateAnimationVeo3Tool(unittest.IsolatedAsyncioTestCase):

    @patch.dict(os.environ, {}, clear=True)
    async def test_missing_api_key(self):
        result = await generate_animation_veo3.ainvoke({
            "prompt_text": "cinematic panning shot of baby playing",
            "output_path": "video.mp4"
        })
        self.assertIn("Error: GEMINI_API_KEY environment variable not set", result)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test_gemini_key"})
    async def test_empty_prompt_text(self):
        result = await generate_animation_veo3.ainvoke({
            "prompt_text": "",
            "output_path": "video.mp4"
        })
        self.assertIn("Error: prompt_text cannot be empty", result)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test_gemini_key"})
    @patch("os.path.exists", return_value=False)
    async def test_nonexistent_image_file(self, mock_exists):
        result = await generate_animation_veo3.ainvoke({
            "prompt_text": "cinematic shot",
            "image_path": "nonexistent.png",
            "output_path": "video.mp4"
        })
        self.assertIn("Error: Image file not found at 'nonexistent.png'", result)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test_gemini_key"})
    async def test_empty_output_path(self):
        result = await generate_animation_veo3.ainvoke({
            "prompt_text": "cinematic shot",
            "output_path": ""
        })
        self.assertIn("Error: output_path cannot be empty", result)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test_gemini_key"})
    @patch("os.path.exists", return_value=True)
    @patch("google.genai.Client")
    @patch("google.genai.types.Image.from_file")
    async def test_successful_generation_with_image(self, mock_image_from_file, mock_genai_client, mock_exists):
        mock_client = MagicMock()
        mock_genai_client.return_value = mock_client

        mock_image = MagicMock()
        mock_image_from_file.return_value = mock_image

        mock_operation = MagicMock()
        mock_operation.done = True
        mock_operation.error = None

        mock_video_item = MagicMock()
        mock_video_file = MagicMock()
        mock_video_item.video = mock_video_file
        mock_operation.response.generated_videos = [mock_video_item]

        mock_client.models.generate_videos.return_value = mock_operation

        with patch("os.makedirs"):
            with patch("os.path.abspath", return_value="/mock/path/veo_video.mp4"):
                result = await generate_animation_veo3.ainvoke({
                    "prompt_text": "gentle camera push in, smiling baby",
                    "image_path": "scene.png",
                    "output_path": "veo_video.mp4",
                    "poll_interval": 0.01,
                    "duration": 8,
                    "resolution": "720p"
                })

        mock_image_from_file.assert_called_once_with(location="scene.png")
        mock_client.files.download.assert_called_once_with(file=mock_video_file)
        mock_video_file.save.assert_called_once_with("/mock/path/veo_video.mp4")
        expected = format_tool_response("generate_animation_veo3", payload="/mock/path/veo_video.mp4", errors="None")
        self.assertEqual(result, expected)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test_gemini_key"})
    @patch("google.genai.Client")
    async def test_successful_generation_text_only(self, mock_genai_client):
        mock_client = MagicMock()
        mock_genai_client.return_value = mock_client

        mock_operation = MagicMock()
        mock_operation.done = True
        mock_operation.error = None

        mock_video_item = MagicMock()
        mock_video_file = MagicMock()
        mock_video_item.video = mock_video_file
        mock_operation.response.generated_videos = [mock_video_item]

        mock_client.models.generate_videos.return_value = mock_operation

        with patch("os.makedirs"):
            with patch("os.path.abspath", return_value="/mock/path/veo_video.mp4"):
                result = await generate_animation_veo3.ainvoke({
                    "prompt_text": "calico kitten playing in sunshine with dialogue",
                    "output_path": "veo_video.mp4",
                    "poll_interval": 0.01
                })

        mock_client.files.download.assert_called_once_with(file=mock_video_file)
        mock_video_file.save.assert_called_once_with("/mock/path/veo_video.mp4")
        expected = format_tool_response("generate_animation_veo3", payload="/mock/path/veo_video.mp4", errors="None")
        self.assertEqual(result, expected)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test_gemini_key"})
    @patch("google.genai.Client")
    async def test_operation_error(self, mock_genai_client):
        mock_client = MagicMock()
        mock_genai_client.return_value = mock_client

        mock_operation = MagicMock()
        mock_operation.done = True
        mock_operation.error = "Prompt violated safety guidelines"
        mock_client.models.generate_videos.return_value = mock_operation

        result = await generate_animation_veo3.ainvoke({
            "prompt_text": "unsafe prompt",
            "output_path": "veo_video.mp4",
            "poll_interval": 0.01
        })

        self.assertIn("Veo video generation failed: Prompt violated safety guidelines", result)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test_gemini_key"})
    @patch("google.genai.Client")
    async def test_timeout(self, mock_genai_client):
        mock_client = MagicMock()
        mock_genai_client.return_value = mock_client

        mock_operation = MagicMock()
        mock_operation.done = False
        mock_client.models.generate_videos.return_value = mock_operation
        mock_client.operations.get.return_value = mock_operation

        result = await generate_animation_veo3.ainvoke({
            "prompt_text": "slow generation",
            "output_path": "veo_video.mp4",
            "poll_interval": 0.01,
            "max_wait_seconds": 0.02
        })

        self.assertIn("Timeout waiting for Veo video generation", result)

if __name__ == "__main__":
    unittest.main()
