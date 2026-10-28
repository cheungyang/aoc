import unittest
import os
import sys
from unittest.mock import patch, MagicMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.config.settings import Config
from core.loaders.tools_loader import FOLLOW_UP, ToolsLoader, gets_follow_up
from core.runtime.foundation.sessions import SessionManager
from tests.helpers import make_context


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
        tools = loader.get_tools(make_context(agent_id="test_agent"))
        
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].__name__, "git")

    @patch('importlib.import_module')
    @patch('os.listdir')
    @patch('os.path.isdir')
    @patch('os.path.isfile')
    def test_load_tools_sorted(self, mock_isfile, mock_isdir, mock_listdir, mock_import):
        from core.loaders.agents_loader import AgentsLoader
        mock_loader = MagicMock()
        AgentsLoader._instance = mock_loader
        mock_agent = MagicMock()
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.config = {"tools": {"zebra": {}, "apple": {}}}

        mock_isdir.side_effect = lambda p: p.endswith("tools")
        mock_listdir.side_effect = lambda p: ["zebra.py", "apple.py"] if p.endswith("tools") else []
        mock_isfile.side_effect = lambda p: p.endswith(".py")

        def import_side_effect(mod_name):
            mod = MagicMock()
            tool_name = mod_name.split(".")[-1]
            func = MagicMock()
            func.name = tool_name
            setattr(mod, tool_name, func)
            return mod
        mock_import.side_effect = import_side_effect

        loader = ToolsLoader()
        loader._discovered_tools = None
        tools = loader.get_tools(make_context(agent_id="test_agent"))

        self.assertEqual(len(tools), 2)
        tool_names = [t.name for t in tools]
        self.assertEqual(tool_names, ["apple", "zebra"])

    @patch('core.loaders.skills_loader.SkillsLoader')
    @patch('core.loaders.agents_loader.AgentsLoader')
    def test_merge_tool_permissions(self, mock_agents_loader, mock_skills_loader):
        mock_agent = MagicMock()
        mock_agent.config = {"tools": {"git": {}}}
        mock_agents_loader.return_value.get_agent.return_value = mock_agent

        mock_skills_inst = mock_skills_loader.return_value
        mock_skills_inst.resolve_allowed_skills.return_value = ["research"]
        mock_skills_inst.get_skill_tools.return_value = {"bash": {}}

        loader = ToolsLoader()
        merged = loader._merge_tool_permissions(make_context(agent_id="agent1"))
        
        self.assertIn("git", merged)
        self.assertIn("bash", merged)
        mock_skills_inst.resolve_allowed_skills.assert_called_once_with("agent1", None)

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
        
        self.assertTrue(self.loader.check_permission(make_context(agent_id="test_agent"), "generic_tool", "read", path=target_child))
        self.assertTrue(self.loader.check_permission(make_context(agent_id="test_agent"), "generic_tool", "write", path=target_child))
        self.assertFalse(self.loader.check_permission(make_context(agent_id="test_agent"), "generic_tool", "delete", path=target_child))
        
        target_parent = os.path.join(workspace_root, "pkm", "note.md")
        self.assertTrue(self.loader.check_permission(make_context(agent_id="test_agent"), "generic_tool", "read", path=target_parent))
        self.assertFalse(self.loader.check_permission(make_context(agent_id="test_agent"), "generic_tool", "write", path=target_parent))



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
        self.assertTrue(self.loader.check_permission(make_context(agent_id="agent1"), "generic_tool", "read", path=target_agent1))
        
        self.assertFalse(self.loader.check_permission(make_context(agent_id="agent2"), "generic_tool", "read", path=target_agent1))

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
        self.assertTrue(self.loader.check_permission(make_context(agent_id="agent1"), "filesystem", "read", path=target_agent1))
        
        self.assertFalse(self.loader.check_permission(make_context(agent_id="agent2"), "filesystem", "read", path=target_agent1))

    @patch.object(ToolsLoader, '_merge_tool_permissions')
    def test_check_permission_tool_level(self, mock_merge):
        mock_merge.return_value = {
            "graph_call": {},
            "agent_call": {}
        }
        self.assertTrue(self.loader.check_permission(make_context(agent_id="agent1"), "graph_call"))
        self.assertTrue(self.loader.check_permission(make_context(agent_id="agent1"), "agent_call"))
        self.assertFalse(self.loader.check_permission(make_context(agent_id="agent1"), "bash"))

    @patch('core.loaders.graphs_loader.GraphsLoader.get_graph_tools')
    @patch('core.loaders.graphs_loader.GraphsLoader.get_graph_skills')
    @patch('core.loaders.skills_loader.SkillsLoader.get_skill_tools')
    @patch('core.loaders.agents_loader.AgentsLoader.get_agent')
    def test_merge_graph_tool_and_skill_permissions(self, mock_get_agent, mock_get_skill_tools, mock_get_graph_skills, mock_get_graph_tools):
        # Mock agent base config
        mock_agent = MagicMock()
        mock_agent.config = {
            "tools": {
                "filesystem": {
                    "pkm/agents/<agent_id>": ["read"]
                }
            },
            "skills": {}
        }
        mock_get_agent.return_value = mock_agent

        # Mock graph tools and skills
        mock_get_graph_tools.side_effect = lambda g: {
            "git": {},
            "filesystem": {
                "sessions": ["read", "write"]
            }
        } if g == "test_graph" else {}
        mock_get_graph_skills.side_effect = lambda g: ["custom_graph_skill"] if g == "test_graph" else []
        mock_get_skill_tools.side_effect = lambda s: {"custom_tool": {}} if s == "custom_graph_skill" else {}

        # 1. Without active graph
        perms_no_graph = self.loader._merge_tool_permissions(make_context(agent_id="agent1"))
        self.assertNotIn("git", perms_no_graph)
        self.assertNotIn("custom_tool", perms_no_graph)

        # 2. With the graph binding carried on the execution context (not ambient state)
        self.loader.clear_permissions_cache()
        perms_with_graph = self.loader._merge_tool_permissions(
            make_context(agent_id="agent1", graph_id="test_graph")
        )
        self.assertIn("git", perms_with_graph)
        self.assertIn("custom_tool", perms_with_graph)
        self.assertIn("filesystem", perms_with_graph)
        self.assertIn("sessions", perms_with_graph["filesystem"])
        self.assertEqual(perms_with_graph["filesystem"]["sessions"], ["read", "write"])

        # 3. An ambient context must NOT leak into a differently-bound call: the roster is
        # decided by the ctx argument alone. This is the frozen-tool-roster regression guard.
        from core.runtime.foundation.context import current_execution_context
        token = current_execution_context.set(make_context(agent_id="agent1", graph_id="test_graph"))
        try:
            self.loader.clear_permissions_cache()
            perms_unbound = self.loader._merge_tool_permissions(make_context(agent_id="agent1"))
            self.assertNotIn("git", perms_unbound)
            self.assertNotIn("custom_tool", perms_unbound)
        finally:
            current_execution_context.reset(token)

    @patch('core.loaders.agents_loader.AgentsLoader.get_agent')
    def test_default_pkm_workspace_permission(self, mock_get_agent):
        from core.config.settings import Config
        mock_agent = MagicMock()
        mock_agent.config = {"tools": {}}
        mock_get_agent.return_value = mock_agent

        pkm_dir = Config().pkm_dir
        expected_pkm_path = os.path.join(pkm_dir, "agents", "researcher")
        unexpected_code_path = "agents/researcher"

        perms = self.loader._merge_tool_permissions(make_context(agent_id="researcher"))
        self.assertIn("filesystem", perms)
        self.assertIn(expected_pkm_path, perms["filesystem"])
        self.assertNotIn(unexpected_code_path, perms["filesystem"])
        self.assertIn("append", perms["filesystem"][expected_pkm_path])
        self.assertIn("read", perms["filesystem"][expected_pkm_path])

    @patch.object(ToolsLoader, '_merge_tool_permissions')
    def test_check_permission_absolute_path(self, mock_merge):
        abs_allowed_dir = "/var/data/custom_agent_vault"
        mock_merge.return_value = {
            "filesystem": {
                abs_allowed_dir: ["read", "write", "append"]
            }
        }

        # Subfile in allowed absolute directory
        target_file = os.path.join(abs_allowed_dir, "logs", "2026-08-24.md")
        self.assertTrue(self.loader.check_permission(make_context(agent_id="agent1"), "filesystem", "append", path=target_file))
        self.assertTrue(self.loader.check_permission(make_context(agent_id="agent1"), "filesystem", "read", path=target_file))
        self.assertFalse(self.loader.check_permission(make_context(agent_id="agent1"), "filesystem", "delete", path=target_file))

        # Outside directory (with similar prefix name)
        outside_file = "/var/data/custom_agent_vault_other/secret.txt"
        self.assertFalse(self.loader.check_permission(make_context(agent_id="agent1"), "filesystem", "read", path=outside_file))

    @patch.object(ToolsLoader, '_merge_tool_permissions')
    def test_check_permission_workspace_root_relative_path(self, mock_merge):
        mock_merge.return_value = {
            "filesystem": {
                "pkm/wiki": ["read", "write"]
            }
        }
        workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

        # Valid subfile
        valid_path = os.path.join(workspace_root, "pkm", "wiki", "index.md")
        self.assertTrue(self.loader.check_permission(make_context(agent_id="agent1"), "filesystem", "read", path=valid_path))
        self.assertTrue(self.loader.check_permission(make_context(agent_id="agent1"), "filesystem", "write", path=valid_path))
        self.assertFalse(self.loader.check_permission(make_context(agent_id="agent1"), "filesystem", "delete", path=valid_path))

        # Sibling directory with similar prefix (should not match)
        sibling_path = os.path.join(workspace_root, "pkm", "wiki_gardener", "index.md")
        self.assertFalse(self.loader.check_permission(make_context(agent_id="agent1"), "filesystem", "read", path=sibling_path))


class TestFollowUpTool(unittest.TestCase):
    """follow_up is added only with the switch on, to a run that keeps memory."""

    CONFIG = {"channels": ["lifestyle-perk", "general"]}

    def setUp(self):
        ToolsLoader._instance = None
        self.addCleanup(setattr, ToolsLoader, "_instance", None)

    @staticmethod
    def _ctx(**overrides):
        values = dict(agent_id="reward-travel", source="discord", channel="lifestyle-perk", stateless=False)
        values.update(overrides)
        return SessionManager.get_session(**values)

    def test_it_needs_the_switch(self):
        self.assertFalse(gets_follow_up(self._ctx(), self.CONFIG))
        with Config().overriding(proactive_delivery=True):
            self.assertTrue(gets_follow_up(self._ctx(), self.CONFIG))

    def test_only_a_run_that_keeps_memory_gets_it(self):
        excluded = {
            "a stateless agent": (self._ctx(), dict(self.CONFIG, stateless=True)),
            "a stateless run": (self._ctx(stateless=True), self.CONFIG),
            "a job": (self._ctx(source="job", channel=None, stateless=True), self.CONFIG),
            "a dream or a replay": (self._ctx(record_memory=False), self.CONFIG),
            "a graph step": (self._ctx(graph_id="coding"), self.CONFIG),
            "the Concierge": (self._ctx(agent_id="main", channel="general"), {"channels": ["*"]}),
            "an opt-out": (self._ctx(), dict(self.CONFIG, proactivity={"follow_ups": False})),
            # agent_call: the follow-up belongs to the agent you're talking to, not to this one.
            "a run another agent asked for": (self._ctx(source="tool", called_by_agent=True), self.CONFIG),
        }
        with Config().overriding(proactive_delivery=True):
            for name, (ctx, config) in excluded.items():
                with self.subTest(name):
                    self.assertFalse(gets_follow_up(ctx, config))
            for name, ctx, config in (
                ("a message", self._ctx(), self.CONFIG),
                ("a spoken turn", self._ctx(source="voice"), self.CONFIG),
                ("the router's hand-over", self._ctx(source="tool"), self.CONFIG),
                ("proactivity without the opt-out", self._ctx(), dict(self.CONFIG, proactivity={"offers": ["x"]})),
            ):
                with self.subTest(name):
                    self.assertTrue(gets_follow_up(ctx, config))

    @patch("core.loaders.skills_loader.SkillsLoader.needs_load_skill", return_value=False)
    @patch("core.loaders.agents_loader.AgentsLoader.get_agent_config")
    def test_the_roster_has_it_only_where_the_predicate_says(self, get_config, _needs_load_skill):
        get_config.return_value = self.CONFIG
        loader = ToolsLoader()
        granted = {"set_thinking": {}, FOLLOW_UP: {}}  # a grant naming it adds it nowhere else

        def roster(ctx):
            with patch.object(ToolsLoader, "_merge_tool_permissions", return_value=dict(granted)):
                return [t.name for t in loader.get_tools(ctx)]

        self.assertEqual(roster(self._ctx()), ["set_thinking"])  # the switch is off
        with Config().overriding(proactive_delivery=True):
            self.assertEqual(roster(self._ctx()), [FOLLOW_UP, "set_thinking"])
            self.assertEqual(roster(self._ctx(record_memory=False)), ["set_thinking"])


if __name__ == "__main__":
    unittest.main()

