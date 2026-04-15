import unittest
from unittest.mock import patch, mock_open, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.filesystem import filesystem
from core.util import format_tool_response

class TestFilesystemTool(unittest.TestCase):

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data="file content")
    def test_read_allowed(self, mock_file, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        
        instructions = [{"action": "read", "path": "allowed_folder/file.txt"}]
        result = filesystem.func(agent_id="test_agent", instructions=instructions)
        
        expected_payload = '<instruction_result action="read" path="allowed_folder/file.txt">file content</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    def test_permission_denied(self, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = False
        
        instructions = [{"action": "read", "path": "secret/file.txt"}]
        result = filesystem.func(agent_id="test_agent", instructions=instructions)
        
        expected_errors = '<instruction_error action="read" path="secret/file.txt">Error: Agent test_agent does not have permission to perform \'read\' on path secret/file.txt</instruction_error>'
        expected = format_tool_response("filesystem", payload="", errors=expected_errors)
        self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.makedirs')
    @patch('builtins.open', new_callable=mock_open)
    def test_write_creates_dirs_if_not_exists(self, mock_file, mock_makedirs, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = False
        
        instructions = [{"action": "write", "path": "allowed_folder/new_dir/file.txt", "content": "data"}]
        result = filesystem.func(agent_id="test_agent", instructions=instructions)
        
        expected_payload = '<instruction_result action="write" path="allowed_folder/new_dir/file.txt">Successfully wrote to allowed_folder/new_dir/file.txt</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)
        mock_makedirs.assert_called_once()

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    def test_write_fails_if_exists(self, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        
        instructions = [{"action": "write", "path": "allowed_folder/file.txt", "content": "data"}]
        result = filesystem.func(agent_id="test_agent", instructions=instructions)
        
        expected_errors = '<instruction_error action="write" path="allowed_folder/file.txt">Error: File already exists at allowed_folder/file.txt. Use \'overwrite\' if intentional.</instruction_error>'
        expected = format_tool_response("filesystem", payload="", errors=expected_errors)
        self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.makedirs')
    @patch('builtins.open', new_callable=mock_open)
    def test_overwrite_succeeds_if_exists(self, mock_file, mock_makedirs, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        
        instructions = [{"action": "overwrite", "path": "allowed_folder/file.txt", "content": "data"}]
        result = filesystem.func(agent_id="test_agent", instructions=instructions)
        
        expected_payload = '<instruction_result action="overwrite" path="allowed_folder/file.txt">Successfully overwrote allowed_folder/file.txt</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.listdir')
    def test_ls_allowed(self, mock_listdir, mock_isdir, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isdir.return_value = True
        mock_listdir.return_value = ["file1.txt", "folder1"]
        
        instructions = [{"action": "ls", "path": "allowed_folder/"}]
        result = filesystem.func(agent_id="test_agent", instructions=instructions)
        
        expected_payload = '<instruction_result action="ls" path="allowed_folder/">file1.txt\nfolder1</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.listdir')
    def test_ls_empty_dir(self, mock_listdir, mock_isdir, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isdir.return_value = True
        mock_listdir.return_value = []
        
        instructions = [{"action": "ls", "path": "allowed_folder/"}]
        result = filesystem.func(agent_id="test_agent", instructions=instructions)
        
        expected_payload = '<instruction_result action="ls" path="allowed_folder/">Directory is empty</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
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
        
        instructions = [{"action": "delete", "path": "allowed_folder/file.txt"}]
        result = filesystem.func(agent_id="test_agent", instructions=instructions)
        
        expected_payload = '<instruction_result action="delete" path="allowed_folder/file.txt">Successfully deleted file allowed_folder/file.txt</instruction_result>'
        expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
        self.assertEqual(result, expected)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    def test_delete_blocked_directory(self, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = False
        
        instructions = [{"action": "delete", "path": "allowed_folder"}]
        result = filesystem.func(agent_id="test_agent", instructions=instructions)
        
        expected_errors = '<instruction_error action="delete" path="allowed_folder">Error: Path allowed_folder is not a file. \'delete\' action only deletes single files.</instruction_error>'
        expected = format_tool_response("filesystem", payload="", errors=expected_errors)
        self.assertEqual(result, expected)

    def test_limit_instructions(self):
        instructions = [{"action": "read", "path": f"path{i}.txt"} for i in range(11)]
        result = filesystem.func(agent_id="test_agent", instructions=instructions)
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
                {"action": "read", "path": "file1.txt"},
                {"action": "read", "path": "file2.txt"}
            ]
            result = filesystem.func(agent_id="test_agent", instructions=instructions)
            
            expected_payload = '<instruction_result action="read" path="file1.txt">content1</instruction_result>\n<instruction_result action="read" path="file2.txt">content2</instruction_result>'
            expected = format_tool_response("filesystem", payload=expected_payload, errors="None")
            self.assertEqual(result, expected)

if __name__ == '__main__':
    unittest.main()
