import unittest
import sys
from unittest.mock import patch, MagicMock

# Create mock modules
mock_pytesseract = MagicMock()
mock_PIL = MagicMock()
mock_Image = MagicMock()
mock_PIL.Image = mock_Image

from tools.video_ocr_validator import video_ocr_validator

class TestVideoOcrValidator(unittest.TestCase):
    @patch.dict('sys.modules', {'pytesseract': mock_pytesseract, 'PIL': mock_PIL})
    @patch("os.path.exists", return_value=True)
    def test_video_ocr_validator_success(self, mock_exists):
        mock_pytesseract.image_to_string.return_value = "hello world!"
        
        res = video_ocr_validator.invoke({"frame_paths": ["/fake/frame1.jpg"], "expected_text": ["hello"]})
        self.assertIn("all_expected_text_found", res)
        self.assertIn("true", res.lower())

    @patch.dict('sys.modules', {'pytesseract': mock_pytesseract, 'PIL': mock_PIL})
    @patch("os.path.exists", return_value=True)
    def test_video_ocr_validator_rejected(self, mock_exists):
        mock_pytesseract.image_to_string.return_value = "hello world!"
        
        res = video_ocr_validator.invoke({"frame_paths": ["/fake/frame1.jpg"], "expected_text": ["missing"]})
        self.assertIn("all_expected_text_found", res)
        self.assertIn("false", res.lower())

if __name__ == "__main__":
    unittest.main()
