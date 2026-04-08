import unittest
from unittest.mock import patch, mock_open, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.obsidian import obsidian

class TestObsidianTool(unittest.TestCase):

    @patch('tools.obsidian.AgentsLoader')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data="note content")
    def test_read_with_path(self, mock_file, mock_exists, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent_config.return_value = {
            "tools": { "obsidian": { "pkm-oc": ["read"] } }
        }
        mock_exists.return_value = True # File exists
        
        with patch('os.path.abspath') as mock_abspath:
            def abspath_side_effect(path):
                if "pkm-oc" in path:
                    return "/workspace/pkm-oc/note.md" if "note.md" in path else "/workspace/pkm-oc"
                return "/workspace/" + path
            mock_abspath.side_effect = abspath_side_effect
            
            result = obsidian.func(action="read", vault_id="pkm-oc", agent_id="test_agent", path="note.md")
            self.assertEqual(result, "note content")

    @patch('tools.obsidian.AgentsLoader')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data="note content")
    def test_read_with_args_fallback(self, mock_file, mock_exists, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent_config.return_value = {
            "tools": { "obsidian": { "pkm-oc": ["read"] } }
        }
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

    @patch('tools.obsidian.AgentsLoader')
    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.walk')
    def test_search_no_term_too_many_results(self, mock_walk, mock_isdir, mock_exists, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent_config.return_value = {
            "tools": { "obsidian": { "pkm-oc": ["read"] } }
        }
        mock_exists.return_value = True
        mock_isdir.return_value = True
        
        # Simulate 51 files
        files = [f"file{i}.md" for i in range(51)]
        mock_walk.return_value = [("/workspace/pkm-oc", [], files)]
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = "/workspace/pkm-oc"
            
            result = obsidian.func(action="search", vault_id="pkm-oc", agent_id="test_agent", path="")
            self.assertTrue(result.startswith("Error: Too many results"))

    @patch('tools.obsidian.AgentsLoader')
    @patch('os.path.exists')
    @patch('os.path.isdir')
    @patch('os.walk')
    def test_search_with_term_limit_results(self, mock_walk, mock_isdir, mock_exists, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent_config.return_value = {
            "tools": { "obsidian": { "pkm-oc": ["read"] } }
        }
        mock_exists.return_value = True
        mock_isdir.return_value = True
        
        # Simulate 60 files matching the term
        files = [f"match_{i}.md" for i in range(60)]
        mock_walk.return_value = [("/workspace/pkm-oc", [], files)]
        
        with patch('os.path.abspath') as mock_abspath:
            mock_abspath.return_value = "/workspace/pkm-oc"
            
            result = obsidian.func(action="search", vault_id="pkm-oc", agent_id="test_agent", path="", obsidian_args="match")
            lines = result.split("\n")
            self.assertEqual(len(lines), 51)
            self.assertEqual(lines[-1], "show 50 out of 60 results")

if __name__ == '__main__':
    unittest.main()
