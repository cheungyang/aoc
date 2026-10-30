"""Each agent's own code: the folder mapping, agent-scoped tool loading, unique tool names."""
import importlib
import os
import tempfile
import unittest
from unittest.mock import patch

from core.loaders import agent_code
from core.loaders.tools_loader import ToolsLoader
from tests.helpers import make_context


class TestAgentFolders(unittest.TestCase):

    def test_an_agent_id_maps_to_an_importable_package(self):
        self.assertEqual(agent_code.package_for("fitness-coach"), "fitness_coach")
        self.assertEqual(agent_code.package_for("main"), "main")

    def test_modules_lists_a_kind_folder(self):
        with tempfile.TemporaryDirectory() as root:
            tools = os.path.join(root, "meal-planner", "tools")
            os.makedirs(tools)
            for name in ("kitchen_snapshot.py", "__init__.py", "_helpers.py", "notes.md"):
                open(os.path.join(tools, name), "w").close()
            os.makedirs(os.path.join(root, "main"))  # an agent with no code of its own
            with patch.object(agent_code, "AGENTS_DIR", root):
                self.assertEqual(agent_code.agents_with_code(), ["meal-planner"])
                self.assertEqual(agent_code.modules("meal-planner", "tools"),
                                 {"kitchen_snapshot": "agents.meal_planner.tools.kitchen_snapshot"})
                self.assertEqual(agent_code.modules("meal-planner", "preconditions"), {})

    def test_hyphenated_agent_folders_import_by_package_name(self):
        module = importlib.import_module("agents.fitness_coach.tools.workout_setup")
        self.assertEqual(module.__file__, os.path.join(agent_code.AGENTS_DIR, "fitness-coach", "tools", "workout_setup.py"))
        helpers = importlib.import_module("agents.home_steward.lib.homeassistant.client")
        self.assertIn(os.path.join("home-steward", "lib", "homeassistant"), helpers.__file__)


class TestAgentTools(unittest.TestCase):
    """Against the real tools/ and agents/ folders."""

    def setUp(self):
        ToolsLoader._instance = None
        self.addCleanup(setattr, ToolsLoader, "_instance", None)

    def test_tool_names_are_unique_across_tools_and_agents(self):
        shared = set(ToolsLoader()._discover_tools())
        seen = {}
        for agent_id in agent_code.agents_with_code():
            for name in agent_code.modules(agent_id, "tools"):
                with self.subTest(tool=name):
                    self.assertNotIn(name, shared, f"agents/{agent_id} shadows tools/{name}.py")
                    self.assertNotIn(name, seen, f"{name} is in agents/{agent_id} and agents/{seen.get(name)}")
                seen[name] = agent_id

    def test_an_agents_tool_loads_only_for_that_agent(self):
        loader = ToolsLoader()
        agent_tools = loader._agent_tools()
        self.assertTrue(agent_tools, "no agent tools found")
        tool, (agent_id, module_path) = sorted(agent_tools.items())[0]
        self.assertEqual(loader.module_path(tool), module_path)
        grants = {tool: {}, "hello_world": {}}
        with patch.object(ToolsLoader, "_merge_tool_permissions", return_value=grants), \
                patch("core.loaders.skills_loader.SkillsLoader.needs_load_skill", return_value=False):
            own = [t.name for t in loader.get_tools(make_context(agent_id=agent_id))]
            other = [t.name for t in loader.get_tools(make_context(agent_id="some-other-agent"))]
        self.assertEqual(sorted(own), sorted([tool, "hello_world"]))
        self.assertEqual(other, ["hello_world"], "another agent's grant can't load it")


if __name__ == "__main__":
    unittest.main()
