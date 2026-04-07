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
    @patch('os.path.isfile')
    def test_load_tools(self, mock_isfile, mock_isdir, mock_listdir, mock_import):
        def isdir_side_effect(path):
            if path.endswith("tools"):
                return True
            return False
        mock_isdir.side_effect = isdir_side_effect
        
        def listdir_side_effect(path):
            if path.endswith("tools"):
                return ["git.py"]
            return []
        mock_listdir.side_effect = listdir_side_effect
        
        def isfile_side_effect(path):
            if path.endswith("git.py"):
                return True
            return False
        mock_isfile.side_effect = isfile_side_effect
        
        mock_module = MagicMock()
        mock_func = MagicMock()
        mock_func.__name__ = "git"
        setattr(mock_module, "git", mock_func)
        mock_import.return_value = mock_module
        
        loader = ToolLoader()
        loader._discovered_tools = None # Force re-discovery
        tools = loader.get_tools()
        
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].__name__, "git")

if __name__ == "__main__":
    unittest.main()
