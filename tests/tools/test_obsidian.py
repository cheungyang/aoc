import unittest
from unittest.mock import patch, mock_open, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.obsidian import obsidian

class TestObsidianTool(unittest.TestCase):

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data="note content")
    def test_read_with_path(self, mock_file, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True # File exists
        
        with patch('os.path.abspath') as mock_abspath:
            def abspath_side_effect(path):
                if "pkm-oc" in path:
                    return "/workspace/pkm-oc/note.md" if "note.md" in path else "/workspace/pkm-oc"
                return "/workspace/" + path
            mock_abspath.side_effect = abspath_side_effect
            
            result = obsidian.func(action="read", vault_id="pkm-oc", agent_id="test_agent", path="note.md")
            self.assertEqual(result, "note content")

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data="note content")
    def test_read_with_args_fallback(self, mock_file, mock_exists, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True # File exists
        
        with patch('os.path.abspath') as mock_abspath:
            def abspath_side_effect(path):
                if "pkm-oc" in path:
                    return "/workspace/pkm-oc/note.md" if "note.md" in path else "/workspace/pkm-oc"
                return "/workspace/" + path
            mock_abspath.side_effect = abspath_side_effect
            
            # Call with path="" and args="note.md"
            result = obsidian.func(action="read", vault_id="pkm-oc", agent_id="test_agent", path="", obsidian_args="note.md")
            self.assertEqual(result, "note content")

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
        mock_walk.return_value = [("/workspace/pkm-oc", [], files)]
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = "/workspace/pkm-oc"
            
            result = obsidian.func(action="file_search", vault_id="pkm-oc", agent_id="test_agent", path="")
            self.assertTrue(result.startswith("Error: Too many results"))

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
        mock_walk.return_value = [("/workspace/pkm-oc", [], files)]
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = "/workspace/pkm-oc"
            
            result = obsidian.func(action="file_search", vault_id="pkm-oc", agent_id="test_agent", path="", obsidian_args="match")
            lines = result.split("\n")
            self.assertEqual(len(lines), 51)
            self.assertEqual(lines[-1], "show 50 out of 60 results")

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('tools.obsidian.VaultVectorStore')
    @patch('os.path.exists')
    def test_vector_search(self, mock_exists, mock_vector_store, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        
        mock_store_instance = MagicMock()
        mock_vector_store.return_value = mock_store_instance
        mock_store_instance.search.return_value = [
            {'path': 'note1.md', 'content': 'content1', 'distance': 0.1},
            {'path': 'note2.md', 'content': 'content2', 'distance': 0.2}
        ]
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = "/workspace/pkm-oc"
            
            result = obsidian.func(action="vector_search", vault_id="pkm-oc", agent_id="test_agent", obsidian_args="query")
            self.assertIn("File: note1.md", result)
            self.assertIn("Content: content1", result)
            self.assertIn("Distance: 0.1", result)

    @patch('core.loaders.tools_loader.ToolsLoader')
    @patch('tools.obsidian.VaultVectorStore')
    @patch('os.path.exists')
    def test_update_vectors(self, mock_exists, mock_vector_store, mock_tools_loader):
        mock_loader = MagicMock()
        mock_tools_loader.return_value = mock_loader
        mock_loader.check_permission.return_value = True
        mock_exists.return_value = True
        
        mock_store_instance = MagicMock()
        mock_vector_store.return_value = mock_store_instance
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = "/workspace/pkm-oc"
            
            result = obsidian.func(action="update_vectors", vault_id="pkm-oc", agent_id="test_agent")
            self.assertEqual(result, "Vectors updated successfully.")
            mock_store_instance.index_vault.assert_called_once()

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
        
        result = obsidian.func(action="delete", vault_id="pkm-oc", agent_id="test_agent", path="note.md")
        self.assertEqual(result, "Successfully deleted file note.md")
        workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        expected_path = os.path.join(workspace_root, "pkm-oc", "note.md")
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
        
        result = obsidian.func(action="delete", vault_id="pkm-oc", agent_id="test_agent", path="")
        self.assertIn("Error: Path  is not a file", result)


if __name__ == '__main__':
    unittest.main()
