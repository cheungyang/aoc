import unittest
from unittest.mock import patch, mock_open, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.filesystem import filesystem

class TestFilesystemTool(unittest.TestCase):

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data="file content")
    def test_read_allowed(self, mock_file, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        
        result = filesystem.func(action="read", path="allowed_folder/file.txt", agent_id="test_agent")
        self.assertEqual(result, "file content")


    @patch('core.loaders.tools_loader.ToolsLoader')
    def test_permission_denied(self, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = False
        
        result = filesystem.func(action="read", path="secret/file.txt", agent_id="test_agent")
        self.assertIn("Error: Agent test_agent does not have permission", result)


    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.makedirs')
    @patch('builtins.open', new_callable=mock_open)
    def test_write_creates_dirs_if_not_exists(self, mock_file, mock_makedirs, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = False # File does not exist
        
        result = filesystem.func(action="write", path="allowed_folder/new_dir/file.txt", content="data", agent_id="test_agent")
        self.assertIn("Successfully wrote to", result)
        mock_makedirs.assert_called_once()


    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    def test_write_fails_if_exists(self, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True # File exists
        
        result = filesystem.func(action="write", path="allowed_folder/file.txt", content="data", agent_id="test_agent")
        self.assertIn("Error: File already exists", result)


    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.makedirs')
    @patch('builtins.open', new_callable=mock_open)
    def test_overwrite_succeeds_if_exists(self, mock_file, mock_makedirs, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True # File exists
        
        result = filesystem.func(action="overwrite", path="allowed_folder/file.txt", content="data", agent_id="test_agent")
        self.assertIn("Successfully overwrote", result)
        mock_file.assert_called_once_with("allowed_folder/file.txt", "w")


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
        
        result = filesystem.func(action="ls", path="allowed_folder/", agent_id="test_agent")
        self.assertEqual(result, "file1.txt\nfolder1")


    @patch('tools.filesystem.AgentsLoader')
    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.listdir')
    def test_ls_empty_dir(self, mock_listdir, mock_isdir, mock_exists, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent.return_value.config = {
            "tools": {
                "filesystem": { "allowed_folder/": ["ls"] }
            }
        }
        mock_exists.return_value = True
        mock_isdir.return_value = True
        mock_listdir.return_value = []
        
        with patch('os.path.abspath') as mock_abspath:
            def abspath_side_effect(path):
                if "allowed_folder" in path:
                    return "/workspace/allowed_folder"
                return "/workspace/" + path
            mock_abspath.side_effect = abspath_side_effect
            
            result = filesystem.func(action="ls", path="allowed_folder/", agent_id="test_agent")
            self.assertEqual(result, "Directory is empty")

    @patch('tools.filesystem.AgentsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    @patch('os.remove')
    def test_delete_allowed(self, mock_remove, mock_isfile, mock_exists, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent.return_value.config = {
            "tools": {
                "filesystem": { "allowed_folder/": ["delete"] }
            }
        }
        mock_exists.return_value = True
        mock_isfile.return_value = True
        
        with patch('os.path.abspath') as mock_abspath:
            def abspath_side_effect(path):
                if "allowed_folder" in path:
                    return "/workspace/allowed_folder/file.txt"
                return "/workspace/" + path
            mock_abspath.side_effect = abspath_side_effect
            
            result = filesystem.func(action="delete", path="allowed_folder/file.txt", agent_id="test_agent")
            self.assertEqual(result, "Successfully deleted file allowed_folder/file.txt")
            mock_remove.assert_called_once_with("/workspace/allowed_folder/file.txt")

    @patch('tools.filesystem.AgentsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    def test_delete_blocked_directory(self, mock_isfile, mock_exists, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent.return_value.config = {
            "tools": {
                "filesystem": { "allowed_folder/": ["delete"] }
            }
        }
        mock_exists.return_value = True
        mock_isfile.return_value = False
        
        with patch('os.path.abspath') as mock_abspath:
            def abspath_side_effect(path):
                if "allowed_folder" in path:
                    return "/workspace/allowed_folder"
                return "/workspace/" + path
            mock_abspath.side_effect = abspath_side_effect
            
            result = filesystem.func(action="delete", path="allowed_folder", agent_id="test_agent")
            self.assertIn("Error: Path allowed_folder is not a file", result)

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
        
        result = filesystem.func(action="ls", path="allowed_folder/", agent_id="test_agent")
        self.assertEqual(result, "Directory is empty")

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
        
        result = filesystem.func(action="delete", path="allowed_folder/file.txt", agent_id="test_agent")
        self.assertIn("Successfully deleted file", result)
        mock_remove.assert_called_once_with("allowed_folder/file.txt")

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('os.path.isfile')
    def test_delete_blocked_directory(self, mock_isfile, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        mock_isfile.return_value = False
        
        result = filesystem.func(action="delete", path="allowed_folder", agent_id="test_agent")
        self.assertIn("Error: Path allowed_folder is not a file", result)

if __name__ == '__main__':
    unittest.main()
