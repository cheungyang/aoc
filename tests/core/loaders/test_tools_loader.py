import unittest
import os
import sys
from unittest.mock import patch, MagicMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.loaders.tools_loader import ToolsLoader


class TestToolsLoader(unittest.TestCase):
    def setUp(self):
        ToolsLoader._instance = None # Reset singleton
        from core.loaders.agents_loader import AgentsLoader
        self.original_agents_loader_instance = AgentsLoader._instance

    def tearDown(self):
        from core.loaders.agents_loader import AgentsLoader
        AgentsLoader._instance = self.original_agents_loader_instance

    @patch('importlib.import_module')
    @patch('os.listdir')
    @patch('os.path.isdir')
    @patch('os.path.isfile')
    def test_load_tools(self, mock_isfile, mock_isdir, mock_listdir, mock_import):
        from core.loaders.agents_loader import AgentsLoader
        
        # Setup mock for AgentsLoader singleton instance
        mock_loader = MagicMock()
        AgentsLoader._instance = mock_loader
        mock_agent = MagicMock()
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.config = {"tools": {"git": {}}}
        
        def isdir_side_effect(path):
            if path.endswith("tools"):
                return True
            return False
        mock_isdir.side_effect = isdir_side_effect
        
        def listdir_side_effect(path):
            if path.endswith("tools"):
                return ["git.py"]
            return []
        mock_listdir.side_effect = listdir_side_effect
        
        def isfile_side_effect(path):
            if path.endswith("git.py"):
                return True
            return False
        mock_isfile.side_effect = isfile_side_effect
        
        mock_module = MagicMock()
        mock_func = MagicMock()
        mock_func.__name__ = "git"
        setattr(mock_module, "git", mock_func)
        mock_import.return_value = mock_module
        
        loader = ToolsLoader()
        loader._discovered_tools = None # Force re-discovery
        tools = loader.get_tools(agent_id="test_agent")
        
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].__name__, "git")

    @patch('core.loaders.skills_loader.SkillsLoader')
    @patch('core.loaders.agents_loader.AgentsLoader')
    def test_merge_tool_permissions(self, mock_agents_loader, mock_skills_loader):
        mock_agent = MagicMock()
        mock_agent.config = {"tools": {"git": {}}}
        mock_agents_loader.return_value.get_agent.return_value = mock_agent

        mock_skills_inst = mock_skills_loader.return_value
        mock_skills_inst.get_allowed_skills.return_value = ["dream"]
        mock_skills_inst.get_skill_tools.return_value = {"bash": {}}

        loader = ToolsLoader()
        merged = loader._merge_tool_permissions("agent1")
        
        self.assertIn("git", merged)
        self.assertIn("bash", merged)
        mock_skills_inst.get_allowed_skills.assert_called_once_with("agent1")

class TestCheckPermission(unittest.TestCase):
    def setUp(self):
        ToolsLoader._instance = None
        self.loader = ToolsLoader()

    @patch.object(ToolsLoader, '_merge_tool_permissions')
    def test_merge_permissions_overlapping_paths(self, mock_merge):
        mock_merge.return_value = {
            "generic_tool": {
                "pkm": ["read"],
                "pkm/wiki": ["write"]
            }
        }
        # Go up 3 levels from tests/core/loaders to reach workspace root
        workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        target_child = os.path.join(workspace_root, "pkm", "wiki", "note.md")
        
        self.assertTrue(self.loader.check_permission("test_agent", "generic_tool", "read", path=target_child))
        self.assertTrue(self.loader.check_permission("test_agent", "generic_tool", "write", path=target_child))
        self.assertFalse(self.loader.check_permission("test_agent", "generic_tool", "delete", path=target_child))
        
        target_parent = os.path.join(workspace_root, "pkm", "note.md")
        self.assertTrue(self.loader.check_permission("test_agent", "generic_tool", "read", path=target_parent))
        self.assertFalse(self.loader.check_permission("test_agent", "generic_tool", "write", path=target_parent))



    @patch.object(ToolsLoader, '_merge_tool_permissions')
    def test_agent_id_placeholder_replacement(self, mock_merge):
        mock_merge.return_value = {
            "generic_tool": {
                "pkm/agents/<agent_id>": ["read"]
            }
        }
        # Go up 3 levels from tests/core/loaders to reach workspace root
        workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        
        target_agent1 = os.path.join(workspace_root, "pkm", "agents", "agent1", "file.txt")
        self.assertTrue(self.loader.check_permission("agent1", "generic_tool", "read", path=target_agent1))
        
        self.assertFalse(self.loader.check_permission("agent2", "generic_tool", "read", path=target_agent1))

    @patch.object(ToolsLoader, '_merge_tool_permissions')
    def test_filesystem_agent_id_placeholder(self, mock_merge):
        mock_merge.return_value = {
            "filesystem": {
                "pkm/agents/<agent_id>": ["read"]
            }
        }
        # Go up 3 levels from tests/core/loaders to reach workspace root
        workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        
        target_agent1 = os.path.join(workspace_root, "pkm", "agents", "agent1", "file.txt")
        self.assertTrue(self.loader.check_permission("agent1", "filesystem", "read", path=target_agent1))
        
        self.assertFalse(self.loader.check_permission("agent2", "filesystem", "read", path=target_agent1))

    @patch.object(ToolsLoader, '_merge_tool_permissions')
    def test_check_permission_tool_level(self, mock_merge):
        mock_merge.return_value = {
            "graph_call": {},
            "agent_call": {}
        }
        self.assertTrue(self.loader.check_permission("agent1", "graph_call"))
        self.assertTrue(self.loader.check_permission("agent1", "agent_call"))
        self.assertFalse(self.loader.check_permission("agent1", "bash"))

if __name__ == "__main__":
    unittest.main()

