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
    @patch('core.mcp_manager.ToolLoader')
    def test_run_server_success(self, mock_tool_loader_class, mock_fastmcp_class):
        # Setup FastMCP mock behavior 
        mock_mcp = MagicMock()
        mock_fastmcp_class.return_value = mock_mcp
        
        # Setup ToolLoader mock
        mock_loader = MagicMock()
        mock_tool_loader_class.return_value = mock_loader
        
        mock_tool_func = MagicMock()
        mock_tool_func.__name__ = "test_tool"
        mock_loader.load_tools.return_value = [mock_tool_func]

        # Run
        self.manager.run_server()

        # Assertions
        mock_fastmcp_class.assert_called_once_with("MCP Tool Server")
        mock_loader.load_tools.assert_called_once()
        mock_mcp.add_tool.assert_called_once_with(mock_tool_func)
        mock_mcp.run.assert_called_once()

if __name__ == "__main__":
    unittest.main()
