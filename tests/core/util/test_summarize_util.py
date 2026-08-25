import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import tempfile
import datetime
import re

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)
from core.util.summarize_util import (
    build_heuristic_summary,
    save_agent_memory_log,
    compress_image_bytes,
    SUMMARY_PREFIX,
    SUMMARY_SUFFIX,
)


class TestSummarizeUtil(unittest.TestCase):

    def test_compress_image_bytes_valid(self):
        import io
        from PIL import Image

        # Create a large dummy image (2000 x 2000 RGBA)
        img = Image.new("RGBA", (2000, 2000), color=(255, 0, 0, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw_bytes = buf.getvalue()

        compressed, mime = compress_image_bytes(raw_bytes, max_dim=1000, quality=75)
        self.assertEqual(mime, "image/jpeg")
        self.assertTrue(len(compressed) > 0)
        self.assertTrue(len(compressed) < len(raw_bytes))

        # Check resized dimensions
        with Image.open(io.BytesIO(compressed)) as res_img:
            self.assertEqual(res_img.size, (1000, 1000))

    def test_compress_image_bytes_empty_and_corrupt(self):
        self.assertEqual(compress_image_bytes(b"")[0], b"")
        self.assertEqual(compress_image_bytes(None)[0], None)
        
        # Corrupt data returns original bytes
        corrupt = b"not_a_real_image"
        out_bytes, mime = compress_image_bytes(corrupt)
        self.assertEqual(out_bytes, corrupt)

    def test_build_heuristic_summary(self):
        older = [
            HumanMessage(content="Please plan a trip to Kyoto."),
            AIMessage(
                content="Checking trains and hotels.",
                tool_calls=[{"name": "hotel_search", "args": {"city": "Kyoto"}, "id": "c1"}]
            ),
            ToolMessage(content="Found Hotel Granvia Kyoto.", name="hotel_search", tool_call_id="c1"),
            AIMessage(content="Hotel Granvia Kyoto is available for 35,000 points.")
        ]
        summary = build_heuristic_summary(older, previous_summary="User previously checked flights.")
        self.assertIn("User previously checked flights", summary)
        self.assertIn("Kyoto", summary)
        self.assertIn("hotel_search", summary)

    def test_save_agent_memory_log(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch('core.util.config.Config.pkm_dir', new=tmp_dir):
                today_str = datetime.datetime.now().strftime("%Y-%m-%d")
                log_content = "- [MEMORY] Task completed."
                
                log_file = save_agent_memory_log("topic-researcher", log_content)
                self.assertIsNotNone(log_file)
                self.assertTrue(os.path.exists(log_file))
                
                with open(log_file, "r") as f:
                    content = f.read()
                self.assertTrue(re.search(r"^- \[\d{2}:\d{2}:\d{2}\] \[MEMORY\] Task completed\.\n$", content))

                # Test append via filesystem tool and placeholder stripping
                save_agent_memory_log("topic-researcher", "- [00:00:00] [FEEDBACK] Added follow-up.")
                with open(log_file, "r") as f:
                    content_after = f.read()
                self.assertTrue(re.search(r"- \[\d{2}:\d{2}:\d{2}\] \[FEEDBACK\] Added follow-up\.\n", content_after))

    def test_save_agent_memory_log_empty(self):
        self.assertIsNone(save_agent_memory_log("topic-researcher", ""))
        self.assertIsNone(save_agent_memory_log("topic-researcher", "   \n\n  "))
        self.assertIsNone(save_agent_memory_log("", "- [12:00:00] [MEMORY] Task"))
        self.assertIsNone(save_agent_memory_log(None, "- [12:00:00] [MEMORY] Task"))

    def test_save_agent_memory_log_tool_error(self):
        error_response = """<filesystem_response>
  <payload></payload>
  <errors><instruction_error action="append" path="/bad/path">Error: Permission Denied</instruction_error></errors>
</filesystem_response>"""

        with patch('tools.filesystem.filesystem') as mock_fs:
            mock_fs.invoke.return_value = error_response
            result = save_agent_memory_log("topic-researcher", "- [12:00:00] [MEMORY] Task")
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
