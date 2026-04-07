import unittest
from unittest.mock import patch, mock_open, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.obsidian import obsidian

class TestObsidianTool(unittest.TestCase):

    def setUp(self):
        self.abspath_patcher = patch('os.path.abspath')
        self.mock_abspath = self.abspath_patcher.start()
        
        def abspath_side_effect(path):
            if path.startswith("/workspace"):
                return path
            return "/workspace/" + path
        self.mock_abspath.side_effect = abspath_side_effect

    def tearDown(self):
        self.abspath_patcher.stop()

    @patch('tools.obsidian.AgentsLoader')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data="file content")
    def test_read_allowed(self, mock_file, mock_exists, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent_config.return_value = {
            "tools": {
                "obsidian": { "pkm-oc": ["read"] }
            }
        }
        mock_exists.return_value = True
        
        result = obsidian(action="read", vault_id="pkm-oc", path="note.md", agent_id="test_agent")
        self.assertEqual(result, "file content")

    @patch('tools.obsidian.AgentsLoader')
    def test_permission_denied_vault(self, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent_config.return_value = {
            "tools": {
                "obsidian": { "pkm-oc": ["read"] }
            }
        }
        
        result = obsidian(action="read", vault_id="other-vault", path="note.md", agent_id="test_agent")
        self.assertIn("Error: Agent test_agent does not have permission for vault other-vault", result)

    @patch('tools.obsidian.AgentsLoader')
    @patch('os.path.exists')
    def test_permission_denied_action(self, mock_exists, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent_config.return_value = {
            "tools": {
                "obsidian": { "pkm-oc": ["read"] }
            }
        }
        mock_exists.return_value = True
        
        result = obsidian(action="write", vault_id="pkm-oc", path="note.md", agent_id="test_agent")
        self.assertIn("Error: Agent test_agent does not have permission to perform 'write'", result)

    @patch('tools.obsidian.AgentsLoader')
    @patch('os.path.exists')
    @patch('os.makedirs')
    @patch('builtins.open', new_callable=mock_open)
    def test_write_allowed(self, mock_file, mock_makedirs, mock_exists, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent_config.return_value = {
            "tools": {
                "obsidian": { "pkm-oc": ["write"] }
            }
        }
        mock_exists.side_effect = [True, False] # Vault exists, file does not exist
        
        result = obsidian(action="write", vault_id="pkm-oc", path="new_note.md", content="new content", agent_id="test_agent")
        self.assertIn("Successfully wrote to", result)

    @patch('tools.obsidian.AgentsLoader')
    @patch('os.path.exists')
    @patch('builtins.open', new_callable=mock_open, read_data="old content")
    def test_append_allowed(self, mock_file, mock_exists, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent_config.return_value = {
            "tools": {
                "obsidian": { "pkm-oc": ["write"] }
            }
        }
        mock_exists.return_value = True # Vault and file exist
        
        result = obsidian(action="append", vault_id="pkm-oc", path="note.md", content="appended content", agent_id="test_agent")
        self.assertIn("Successfully appended to", result)

    @patch('tools.obsidian.AgentsLoader')
    @patch('os.path.exists')
    @patch('os.makedirs')
    @patch('builtins.open', new_callable=mock_open)
    def test_agent_scoped_allowed(self, mock_file, mock_makedirs, mock_exists, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent_config.return_value = {
            "tools": {
                "obsidian": { "pkm-oc": ["agent-scoped"] }
            }
        }
        mock_exists.side_effect = [True, False] # Vault exists, file does not exist
        
        # Path contains agent_id 'test_agent'
        result = obsidian(action="write", vault_id="pkm-oc", path="test_agent/note.md", content="content", agent_id="test_agent")
        self.assertIn("Successfully wrote to", result)

    @patch('tools.obsidian.AgentsLoader')
    @patch('os.path.exists')
    def test_agent_scoped_denied(self, mock_exists, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent_config.return_value = {
            "tools": {
                "obsidian": { "pkm-oc": ["agent-scoped"] }
            }
        }
        mock_exists.return_value = True
        
        # Path does NOT contain agent_id 'test_agent'
        result = obsidian(action="write", vault_id="pkm-oc", path="other_agent/note.md", content="content", agent_id="test_agent")
        self.assertIn("Error: Agent test_agent does not have permission", result)

    @patch('tools.obsidian.AgentsLoader')
    @patch('os.path.exists')
    @patch('os.walk')
    def test_search(self, mock_walk, mock_exists, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent_config.return_value = {
            "tools": {
                "obsidian": { "pkm-oc": ["read"] }
            }
        }
        mock_exists.return_value = True
        
        # Mock os.walk to return some files
        mock_walk.return_value = [
            ("/workspace/pkm-oc", [], ["note1.md", "note2.md", "other.txt"]),
            ("/workspace/pkm-oc/folder", [], ["note3.md"])
        ]
        
        result = obsidian(action="search", vault_id="pkm-oc", path="", args="note", agent_id="test_agent")
        self.assertIn("note1.md", result)
        self.assertIn("note2.md", result)
        self.assertIn("folder/note3.md", result)
        self.assertNotIn("other.txt", result)

if __name__ == '__main__':
    unittest.main()
