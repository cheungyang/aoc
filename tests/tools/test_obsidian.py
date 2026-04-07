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
            
            result = obsidian(action="read", vault_id="pkm-oc", agent_id="test_agent", path="note.md")
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
            result = obsidian(action="read", vault_id="pkm-oc", agent_id="test_agent", path="", args="note.md")
            self.assertEqual(result, "note content")

if __name__ == '__main__':
    unittest.main()
