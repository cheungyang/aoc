import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Inject root (2 levels up from tests/scripts)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from scripts import run_tool

class TestRunToolScript(unittest.TestCase):

    @patch('scripts.run_tool.ToolsLoader')
    def test_main_flow(self, mock_tools_loader):
        mock_loader_instance = MagicMock()
        mock_tools_loader.return_value = mock_loader_instance
        
        # Mock a tool
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.description = "test description"
        mock_tool.args = {'param1': {'type': 'string', 'description': 'param desc'}}
        mock_tool.invoke.return_value = "tool output"
        
        mock_loader_instance.get_tools.return_value = [mock_tool]
        
        # Mock inputs: "1" (select tool 1), "value1" (param1 value)
        inputs = ["1", "value1"]
        
        with patch('builtins.input', side_effect=inputs):
            with patch('builtins.print') as mock_print:
                run_tool.main()
                
                mock_tool.invoke.assert_called_once_with({'param1': 'value1'})
                mock_print.assert_any_call("\n==== Result ====\ntool output\n================")

    @patch('scripts.run_tool.ToolsLoader')
    def test_main_no_tools(self, mock_tools_loader):
        mock_loader_instance = MagicMock()
        mock_tools_loader.return_value = mock_loader_instance
        mock_loader_instance.get_tools.return_value = []
        
        with patch('builtins.print') as mock_print:
            run_tool.main()
            mock_print.assert_any_call("No tools found.")

    @patch('scripts.run_tool.ToolsLoader')
    def test_main_invalid_choice(self, mock_tools_loader):
        mock_loader_instance = MagicMock()
        mock_tools_loader.return_value = mock_loader_instance
        
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.description = "test description"
        mock_loader_instance.get_tools.return_value = [mock_tool]
        
        # Mock inputs: "2" (invalid choice)
        with patch('builtins.input', return_value="2"):
            with patch('builtins.print') as mock_print:
                run_tool.main()
                mock_print.assert_any_call("Invalid choice.")

if __name__ == '__main__':
    unittest.main()
