import unittest
import os
import sys
from unittest.mock import patch, MagicMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.tool_loader import ToolLoader


class TestToolLoader(unittest.TestCase):
    def setUp(self):
        ToolLoader._instance = None # Reset singleton


        
    @patch('importlib.import_module')
    @patch('os.listdir')
    @patch('os.path.isdir')
    def test_load_tools(self, mock_isdir, mock_listdir, mock_import):
        mock_isdir.return_value = True
        def side_effect(path):
            if path.endswith("tools"):
                return ["git"]
            if path.endswith("git"):
                return ["git_command.py"]
            return []
        mock_listdir.side_effect = side_effect
        
        mock_module = MagicMock()
        mock_func = MagicMock()
        mock_func.__name__ = "git_command"
        setattr(mock_module, "git_command", mock_func)
        mock_import.return_value = mock_module
        
        loader = ToolLoader()
        tools = loader.get_tools()
        
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].__name__, "git_command")

if __name__ == "__main__":
    unittest.main()
