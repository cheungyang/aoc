import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.mcp_manager import MCPServerManager

class TestMCPServerManager(unittest.TestCase):

    def setUp(self):
        self.manager = MCPServerManager()

    @patch('core.mcp_manager.FastMCP')
    @patch('core.mcp_manager.os.path.isdir')
    @patch('core.mcp_manager.os.listdir')
    @patch('core.mcp_manager.importlib')
    def test_run_server_success(self, mock_importlib, mock_listdir, mock_isdir, mock_fastmcp_class):
        # Setup FastMCP mock behavior 
        mock_mcp = MagicMock()
        mock_fastmcp_class.return_value = mock_mcp
        
        # Mock decorator behavior mcp.tool()(func)
        mock_decorator = MagicMock()
        mock_mcp.tool.return_value = mock_decorator

        # Setup filesystem mocks
        mock_isdir.side_effect = lambda path: path == "tools" or path == os.path.join("tools", "cat_a")
        mock_listdir.side_effect = lambda path: ['cat_a'] if path == "tools" else ['tool_x.py']
        
        # Mock tool module
        mock_tool_module = MagicMock()
        mock_tool_func = MagicMock()
        setattr(mock_tool_module, "tool_x", mock_tool_func)
        mock_importlib.import_module.return_value = mock_tool_module

        # Run
        self.manager.run_server()

        # Assertions
        mock_fastmcp_class.assert_called_once_with("MCP Tool Server")
        mock_importlib.import_module.assert_called_once_with("tools.cat_a.tool_x")
        mock_mcp.tool.assert_called_once()
        mock_decorator.assert_called_once_with(mock_tool_func)
        mock_mcp.run.assert_called_once()

if __name__ == "__main__":
    unittest.main()
