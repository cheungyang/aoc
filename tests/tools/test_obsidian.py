import unittest
from unittest.mock import patch, mock_open, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.obsidian import obsidian
from core.util import format_tool_response

class TestObsidianTool(unittest.TestCase):

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data="note content")
    def test_read_with_path(self, mock_file, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        
        with patch('os.path.abspath') as mock_abspath:
            def abspath_side_effect(path):
                if "pkm" in path:
                    return "/workspace/pkm/note.md" if "note.md" in path else "/workspace/pkm"
                return "/workspace/" + path
            mock_abspath.side_effect = abspath_side_effect
            
            instructions = [{"action": "read", "path": "note.md"}]
            result = obsidian.func(agent_id="test_agent", vault_id="pkm", instructions=instructions)
            
            expected_payload = '<instruction_result action="read" path="note.md">note content</instruction_result>'
            expected = format_tool_response("obsidian", payload=expected_payload, errors="None")
            self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.walk')
    def test_search_no_term_too_many_results(self, mock_walk, mock_isdir, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isdir.return_value = True
        
        # Simulate 51 files
        files = [f"file{i}.md" for i in range(51)]
        mock_walk.return_value = [("/workspace/pkm", [], files)]
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = "/workspace/pkm"
            
            instructions = [{"action": "file_search", "path": ""}]
            result = obsidian.func(agent_id="test_agent", vault_id="pkm", instructions=instructions)
            
            expected_payload = ''
            expected_errors = '<instruction_error action="file_search" path="">Error: Too many results (51). Please provide a search term to narrow down.</instruction_error>'
            expected = format_tool_response("obsidian", payload=expected_payload, errors=expected_errors)
            self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.walk')
    def test_search_with_term_limit_results(self, mock_walk, mock_isdir, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isdir.return_value = True
        
        # Simulate 60 files matching the term
        files = [f"match_{i}.md" for i in range(60)]
        mock_walk.return_value = [("/workspace/pkm", [], files)]
        
        with patch('os.path.abspath') as mock_abspath:
            def abspath_side_effect(path):
                if path.endswith("pkm"):
                    return "/workspace/pkm"
                return path
            mock_abspath.side_effect = abspath_side_effect
            
            instructions = [{"action": "file_search", "path": "", "content_or_search_term": "match"}]
            result = obsidian.func(agent_id="test_agent", vault_id="pkm", instructions=instructions)
            
            expected_results = [f"match_{i}.md" for i in range(50)]
            expected_output = "\n".join(expected_results)
            expected_output += f"\nshow 50 out of 60 results"
            
            expected_payload = f'<instruction_result action="file_search" path="">{expected_output}</instruction_result>'
            expected = format_tool_response("obsidian", payload=expected_payload, errors="None")
            self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    def test_search_path_not_exists(self, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        
        def exists_side_effect(path):
            if "non_existent" in path:
                return False
            return True
        mock_exists.side_effect = exists_side_effect
        
        with patch('os.path.abspath') as mock_abspath:
            def abspath_side_effect(path):
                if "non_existent" in path:
                    return "/workspace/pkm/non_existent"
                return "/workspace/pkm"
            mock_abspath.side_effect = abspath_side_effect
            
            instructions = [{"action": "file_search", "path": "non_existent"}]
            result = obsidian.func(agent_id="test_agent", vault_id="pkm", instructions=instructions)
            
            expected_payload = ''
            expected_errors = '<instruction_error action="file_search" path="non_existent">Error: Path not found: non_existent</instruction_error>'
            expected = format_tool_response("obsidian", payload=expected_payload, errors=expected_errors)
            self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    @patch('os.remove')
    def test_delete_allowed(self, mock_remove, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = True
        
        instructions = [{"action": "delete", "path": "note.md"}]
        result = obsidian.func(agent_id="test_agent", vault_id="pkm", instructions=instructions)
        
        expected_payload = '<instruction_result action="delete" path="note.md">Successfully deleted file note.md</instruction_result>'
        expected = format_tool_response("obsidian", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)
        
        workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        expected_path = os.path.join(workspace_root, "pkm", "note.md")
        mock_remove.assert_called_once_with(expected_path)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    def test_delete_blocked_directory(self, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = False
        
        instructions = [{"action": "delete", "path": ""}]
        result = obsidian.func(agent_id="test_agent", vault_id="pkm", instructions=instructions)
        
        expected_payload = ''
        expected_errors = '<instruction_error action="delete" path="">Error: Path  is not a file. \'delete\' action only deletes single files.</instruction_error>'
        expected = format_tool_response("obsidian", payload=expected_payload, errors=expected_errors)
        self.assertEqual(result, expected)

    def test_limit_instructions(self):
        instructions = [{"action": "read", "path": f"path{i}.md"} for i in range(11)]
        result = obsidian.func(agent_id="test_agent", vault_id="pkm", instructions=instructions)
        self.assertIn("Error: Too many instructions requested", result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open)
    def test_multi_instruction(self, mock_file, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        
        # Mock file reads
        mock_file.return_value.read.side_effect = ["content1", "content2"]
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.side_effect = lambda x: x # Simple mock
            
            instructions = [
                {"action": "read", "path": "note1.md"},
                {"action": "read", "path": "note2.md"}
            ]
            result = obsidian.func(agent_id="test_agent", vault_id="pkm", instructions=instructions)
            
            expected_payload = '<instruction_result action="read" path="note1.md">content1</instruction_result>\n<instruction_result action="read" path="note2.md">content2</instruction_result>'
            expected = format_tool_response("obsidian", payload=expected_payload, errors="None")
            self.assertEqual(result, expected)

if __name__ == '__main__':
    unittest.main()
