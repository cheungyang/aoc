import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys
import base64

# Inject root directory
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.dalle_image_generator import dalle_image_generator
from core.util import format_tool_response

class TestDalleImageGeneratorTool(unittest.IsolatedAsyncioTestCase):

    @patch.dict(os.environ, {}, clear=True)
    async def test_missing_api_key(self):
        result = await dalle_image_generator.ainvoke({
            "prompt": "cute toddler bear in woods",
            "output_path": "test_output.png"
        })
        self.assertIn("Error: OPENAI_API_KEY environment variable not set", result)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test_openai_key"})
    async def test_empty_prompt(self):
        result = await dalle_image_generator.ainvoke({
            "prompt": "",
            "output_path": "test_output.png"
        })
        self.assertIn("Error: prompt cannot be empty", result)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test_openai_key"})
    async def test_empty_output_path(self):
        result = await dalle_image_generator.ainvoke({
            "prompt": "cute toddler bear",
            "output_path": ""
        })
        self.assertIn("Error: output_path cannot be empty", result)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test_openai_key"})
    @patch("openai.AsyncOpenAI")
    async def test_successful_generation_b64(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_image_item = MagicMock()
        fake_bytes = b"fake_png_image_data"
        fake_b64 = base64.b64encode(fake_bytes).decode("utf-8")
        mock_image_item.b64_json = fake_b64
        mock_image_item.url = None

        mock_response = MagicMock()
        mock_response.data = [mock_image_item]

        mock_client.images.generate = AsyncMock(return_value=mock_response)

        with patch("os.makedirs"):
            with patch("os.path.abspath", return_value="/mock/path/scene_1.png"):
                with patch("builtins.open", unittest.mock.mock_open()) as mock_file:
                    result = await dalle_image_generator.ainvoke({
                        "prompt": "Pixar style happy little fox playing in leaves",
                        "output_path": "scene_1.png",
                        "size": "1024x1024",
                        "quality": "hd",
                        "style": "vivid"
                    })

        mock_client.images.generate.assert_awaited_once_with(
            model="dall-e-3",
            prompt="Pixar style happy little fox playing in leaves",
            size="1024x1024",
            quality="hd",
            style="vivid",
            n=1,
            response_format="b64_json"
        )
        mock_file().write.assert_called_once_with(fake_bytes)
        expected = format_tool_response("dalle_image_generator", payload="/mock/path/scene_1.png", errors="None")
        self.assertEqual(result, expected)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test_openai_key"})
    @patch("openai.AsyncOpenAI")
    async def test_successful_generation_url_fallback(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        mock_image_item = MagicMock()
        mock_image_item.b64_json = None
        mock_image_item.url = "https://images.openai.com/mock_image.png"

        mock_response = MagicMock()
        mock_response.data = [mock_image_item]
        mock_client.images.generate = AsyncMock(return_value=mock_response)

        mock_http_res = MagicMock()
        mock_http_res.content = b"downloaded_png_bytes"
        mock_http_res.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_http_res)
        mock_http_client.__aenter__.return_value = mock_http_client
        mock_http_client.__aexit__.return_value = None

        with patch("httpx.AsyncClient", return_value=mock_http_client):
            with patch("os.makedirs"):
                with patch("os.path.abspath", return_value="/mock/path/scene_1.png"):
                    with patch("builtins.open", unittest.mock.mock_open()) as mock_file:
                        result = await dalle_image_generator.ainvoke({
                            "prompt": "Pixar style baby bunny",
                            "output_path": "scene_1.png"
                        })

        mock_file().write.assert_called_once_with(b"downloaded_png_bytes")
        self.assertIn("/mock/path/scene_1.png", result)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test_openai_key"})
    @patch("openai.AsyncOpenAI")
    async def test_api_failure(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.images.generate = AsyncMock(side_effect=Exception("Rate limit exceeded"))

        result = await dalle_image_generator.ainvoke({
            "prompt": "test prompt",
            "output_path": "test.png"
        })
        self.assertIn("Error generating image with DALL-E: Rate limit exceeded", result)

if __name__ == "__main__":
    unittest.main()
