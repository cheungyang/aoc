"""An agent's memory log (core/knowledge/memory/logs.py): each line stamped with
the time and appended to today's file through the filesystem tool."""
import datetime
import os
import re
import tempfile
import unittest
from unittest.mock import patch

from core.knowledge.memory.logs import save_agent_memory_log


class TestSaveAgentMemoryLog(unittest.TestCase):

    def test_save_agent_memory_log(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch('core.config.settings.Config.pkm_dir', new=tmp_dir):
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
