import unittest
from unittest.mock import patch, MagicMock, ANY
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.generate_image import generate_image
from core.util import format_tool_response

class TestGenerateImageTool(unittest.IsolatedAsyncioTestCase):

    @patch.dict(os.environ, {}, clear=True)
    async def test_missing_api_key(self):
        result = await generate_image.ainvoke({"prompt": "test", "output_path": "test.png"})
        self.assertIn("Error: GEMINI_API_KEY environment variable not set", result)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"})
    async def test_missing_output_path(self):
        with self.assertRaises(Exception):
            await generate_image.ainvoke({"prompt": "test"})

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"})
    @patch('google.genai.Client')
    async def test_successful_generation(self, mock_genai_client):
        mock_client = MagicMock()
        mock_genai_client.return_value = mock_client
        
        mock_response = MagicMock()
        mock_part = MagicMock()
        mock_part.inline_data = MagicMock()
        mock_image = MagicMock()
        mock_part.as_image.return_value = mock_image
        mock_response.parts = [mock_part]
        
        mock_client.models.generate_content.return_value = mock_response
        
        with patch('os.makedirs'):
            with patch('os.path.abspath', return_value="/fake/path/test.png"):
                result = await generate_image.ainvoke({"prompt": "test", "output_path": "test.png"})
                
        mock_client.models.generate_content.assert_called_once_with(
            model="gemini-3.1-flash-image-preview",
            contents=["test"],
        )
        mock_image.save.assert_called_once_with("/fake/path/test.png")
        self.assertEqual(result, format_tool_response("generate_image", payload="/fake/path/test.png", errors="None"))

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"})
    @patch('google.genai.Client')
    async def test_successful_generation_with_image(self, mock_genai_client):
        mock_client = MagicMock()
        mock_genai_client.return_value = mock_client
        
        mock_response = MagicMock()
        mock_part = MagicMock()
        mock_part.inline_data = MagicMock()
        mock_image = MagicMock()
        mock_part.as_image.return_value = mock_image
        mock_response.parts = [mock_part]
        
        mock_client.models.generate_content.return_value = mock_response
        
        # Fake base64 image (a valid base64 string for a tiny 1x1 transparent PNG)
        fake_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        
        with patch('os.makedirs'):
            with patch('os.path.abspath', return_value="/fake/path/test.png"):
                result = await generate_image.ainvoke({
                    "prompt": "test", 
                    "output_path": "test.png",
                    "image_base64": fake_base64
                })
                
        mock_client.models.generate_content.assert_called_once_with(
            model="gemini-3.1-flash-image-preview",
            contents=["test", ANY],
        )
        mock_image.save.assert_called_once_with("/fake/path/test.png")
        self.assertEqual(result, format_tool_response("generate_image", payload="/fake/path/test.png", errors="None"))

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"})
    @patch('google.genai.Client')
    @patch('os.path.exists')
    @patch('PIL.Image.open')
    async def test_successful_generation_with_image_path(self, mock_image_open, mock_exists, mock_genai_client):
        mock_client = MagicMock()
        mock_genai_client.return_value = mock_client
        
        mock_response = MagicMock()
        mock_part = MagicMock()
        mock_part.inline_data = MagicMock()
        mock_image = MagicMock()
        mock_part.as_image.return_value = mock_image
        mock_response.parts = [mock_part]
        
        mock_client.models.generate_content.return_value = mock_response
        mock_exists.return_value = True
        
        mock_input_image = MagicMock()
        mock_image_open.return_value = mock_input_image
        
        with patch('os.makedirs'):
            with patch('os.path.abspath', return_value="/fake/path/test.png"):
                result = await generate_image.ainvoke({
                    "prompt": "test", 
                    "output_path": "test.png",
                    "image_path": "input.png"
                })
                
        mock_client.models.generate_content.assert_called_once_with(
            model="gemini-3.1-flash-image-preview",
            contents=["test", mock_input_image],
        )
        mock_image.save.assert_called_once_with("/fake/path/test.png")
        self.assertEqual(result, format_tool_response("generate_image", payload="/fake/path/test.png", errors="None"))

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"})
    @patch('os.path.exists')
    async def test_image_path_not_exists(self, mock_exists):
        mock_exists.return_value = False
        result = await generate_image.ainvoke({
            "prompt": "test", 
            "output_path": "test.png",
            "image_path": "non_existent.png"
        })
        self.assertIn("Error: Image file not found at non_existent.png", result)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"})

    async def test_invalid_base64(self):
        result = await generate_image.ainvoke({
            "prompt": "test", 
            "output_path": "test.png",
            "image_base64": "invalid_base64"
        })
        self.assertIn("Error decoding input image", result)

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test_key"})
    @patch('google.genai.Client')
    async def test_failed_generation(self, mock_genai_client):
        mock_client = MagicMock()
        mock_genai_client.return_value = mock_client
        mock_client.models.generate_content.side_effect = Exception("API Error")
        
        result = await generate_image.ainvoke({"prompt": "test", "output_path": "test.png"})
        
        self.assertIn("Error generating image: API Error", result)

if __name__ == '__main__':
    unittest.main()

