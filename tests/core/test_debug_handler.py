import unittest
import os
import tempfile
import sys
from unittest.mock import MagicMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.debug_handler import DebugLogHandler

class TestDebugLogHandler(unittest.TestCase):
    def setUp(self):
        self.test_fd, self.test_log_path = tempfile.mkstemp()
        self.handler = DebugLogHandler(log_file=self.test_log_path)

    def tearDown(self):
        os.close(self.test_fd)
        if os.path.exists(self.test_log_path):
            os.remove(self.test_log_path)

    def test_log_writes_to_file(self):
        self.handler._log("Test message")
        with open(self.test_log_path, "r") as f:
            content = f.read()
        self.assertIn("Test message", content)
        self.assertIn("[", content) # Timestamp marker

    def test_on_llm_start(self):
        self.handler.on_llm_start(None, ["Prompt 1", "Prompt 2"])
        with open(self.test_log_path, "r") as f:
            content = f.read()
        self.assertIn("--- LLM START ---", content)
        self.assertIn("Prompt 1", content)
        self.assertIn("Prompt 2", content)

    def test_on_llm_end(self):
        mock_response = MagicMock()
        mock_generation = MagicMock()
        mock_generation.text = "Response text"
        mock_response.generations = [[mock_generation]]
        
        self.handler.on_llm_end(mock_response)
        with open(self.test_log_path, "r") as f:
            content = f.read()
        self.assertIn("--- LLM END ---", content)
        self.assertIn("Response text", content)

    def test_on_tool_start(self):
        self.handler.on_tool_start({"name": "MyTool"}, "input_args")
        with open(self.test_log_path, "r") as f:
            content = f.read()
        self.assertIn("--- TOOL START --- Tool: MyTool", content)
        self.assertIn("input_args", content)

    def test_on_tool_end(self):
        self.handler.on_tool_end("Output description")
        with open(self.test_log_path, "r") as f:
            content = f.read()
        self.assertIn("--- TOOL END --- Tool Output", content)
        self.assertIn("Output description", content)

if __name__ == "__main__":
    unittest.main()
