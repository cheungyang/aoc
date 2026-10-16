import unittest
from unittest.mock import patch, MagicMock, mock_open
import os
import sys
import tempfile
import time

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

try:
    import discord
except ImportError:
    mock_discord = MagicMock()
    mock_ui = MagicMock()
    mock_discord.ui = mock_ui
    sys.modules['discord'] = mock_discord
    sys.modules['discord.ext'] = MagicMock()
    sys.modules['discord.ext.commands'] = MagicMock()
    sys.modules['discord.ui'] = mock_ui

from core.loaders.skills_loader import SkillsLoader, core_section, skill_body
from tests.helpers import make_context
import core.loaders.agents_loader

class TestSkillsLoader(unittest.TestCase):

    def setUp(self):
        SkillsLoader._instance = None
        self.loader = SkillsLoader()

    @patch('core.loaders.skills_loader.os.path.isdir')
    @patch('core.loaders.skills_loader.os.listdir')
    @patch('core.loaders.skills_loader.os.path.isfile')
    @patch('core.loaders.agents_loader.AgentsLoader')
    def test_get_skills_overview(self, mock_agents_loader, mock_isfile, mock_listdir, mock_isdir):
        mock_isdir.return_value = True
        mock_listdir.return_value = ['dummy_skill']
        mock_isfile.return_value = True
        
        mock_agent = MagicMock()
        mock_agent.config = {"skills": {"dummy_skill": {}}}
        mock_agents_loader.return_value.get_agent.return_value = mock_agent
        
        skill_content = '{"name": "Dummy", "description": "This is a dummy skill.", "skill_id": "dummy_skill"}'
        
        with patch('core.loaders.skills_loader.open', mock_open(read_data=skill_content)):
            result = self.loader.get_skills_overview(make_context(agent_id="agent_1"))

        self.assertIn("<skills_list>", result)
        self.assertIn("- Dummy (id:dummy_skill): This is a dummy skill.", result)
        self.assertIn("</skills_list>", result)

    @patch('core.loaders.skills_loader.os.path.isdir')
    @patch('core.loaders.skills_loader.os.listdir')
    @patch('core.loaders.skills_loader.os.path.isfile')
    @patch('core.loaders.agents_loader.AgentsLoader')
    def test_get_skill_prompt(self, mock_agents_loader, mock_isfile, mock_listdir, mock_isdir):
        mock_isdir.return_value = True
        mock_listdir.return_value = ['dummy_skill']
        mock_isfile.return_value = True
        
        mock_agent = MagicMock()
        mock_agent.config = {"skills": {"dummy_skill": {}}}
        mock_agents_loader.return_value.get_agent.return_value = mock_agent
        
        skill_content = """---
name: Dummy
description: This is a dummy skill.
---
Body content of skill.
"""
        
        self.loader._skills_cache['dummy_skill'] = {
            "name": "Dummy",
            "description": "This is a dummy skill.",
            "path": "skills/dummy_skill/SKILL.md"
        }
        
        with patch('core.loaders.skills_loader.open', mock_open(read_data=skill_content)):
            result = self.loader.get_skill_prompt(make_context(agent_id="agent_1"), 'dummy_skill')

        self.assertIn("<skill>", result)
        self.assertIn("Body content of skill.", result)
        self.assertIn("</skill>", result)

    @patch('core.loaders.skills_loader.os.path.isdir')
    @patch('core.loaders.skills_loader.os.listdir')
    @patch('core.loaders.skills_loader.os.path.isfile')
    @patch('core.loaders.agents_loader.AgentsLoader')
    def test_get_skills_overview_filtered(self, mock_agents_loader, mock_isfile, mock_listdir, mock_isdir):
        mock_isdir.return_value = True
        mock_listdir.return_value = ['skill1', 'skill2']
        mock_isfile.return_value = True
        
        mock_agent = MagicMock()
        mock_agent.config = {"skills": {"skill1": {}}}
        mock_agents_loader.return_value.get_agent.return_value = mock_agent
        
        skill_content = '{"name": "Dummy", "description": "Desc", "skill_id": "skill1"}'
        
        with patch('core.loaders.skills_loader.open', mock_open(read_data=skill_content)):
            result = self.loader.get_skills_overview(make_context(agent_id="agent_1"))

        self.assertIn("- Dummy (id:skill1): Desc", result)

    @patch('core.loaders.agents_loader.AgentsLoader')
    def test_get_allowed_skills(self, mock_agents_loader):
        mock_agent = MagicMock()
        mock_agent.config = {"skills": {"skill1": {"preload": "core"}}}
        mock_agents_loader.return_value.get_agent.return_value = mock_agent
        
        allowed_skills = self.loader.get_allowed_skills(make_context(agent_id="agent_1"))
        
        # The main graph grants no skills of its own: the dream standup inlines
        # the dream skill's rules instead of having every agent carry it.
        self.assertEqual(allowed_skills, ["skill1"])

    @patch('core.loaders.graphs_loader.GraphsLoader.get_graph_skills')
    @patch('core.loaders.agents_loader.AgentsLoader')
    def test_get_allowed_skills_with_graph(self, mock_agents_loader, mock_get_graph_skills):
        mock_agent = MagicMock()
        mock_agent.config = {"skills": {"skill1": {}}, "graph": "custom_graph"}
        mock_agents_loader.return_value.get_agent.return_value = mock_agent
        mock_get_graph_skills.return_value = ["graph_skill_1"]
        
        allowed_skills = self.loader.get_allowed_skills(make_context(agent_id="agent_1"))
        
        self.assertIn("skill1", allowed_skills)
        self.assertIn("graph_skill_1", allowed_skills)
        self.assertEqual(len(allowed_skills), 2)

    @patch('core.loaders.agents_loader.AgentsLoader')
    def test_get_skills_overview_sorted(self, mock_agents_loader):
        mock_agent = MagicMock()
        mock_agent.config = {"skills": {"zebra_skill": {}, "apple_skill": {}}}
        mock_agents_loader.return_value.get_agent.return_value = mock_agent

        self.loader._skills_cache['zebra_skill'] = {
            "name": "Zebra Skill",
            "description": "Zebra desc",
            "skill_id": "zebra_skill"
        }
        self.loader._skills_cache['apple_skill'] = {
            "name": "Apple Skill",
            "description": "Apple desc",
            "skill_id": "apple_skill"
        }

        result = self.loader.get_skills_overview(make_context(agent_id="agent_1"))
        pos_apple = result.find("Apple Skill")
        pos_zebra = result.find("Zebra Skill")
        self.assertNotEqual(pos_apple, -1)
        self.assertNotEqual(pos_zebra, -1)
        self.assertLess(pos_apple, pos_zebra, "Expected skills to be listed in alphabetical order")

    @patch('core.loaders.agents_loader.AgentsLoader')
    def test_no_skills_means_no_skills_block(self, mock_agents_loader):
        """An agent with nothing to load gets no header telling it how to load things."""
        mock_agent = MagicMock()
        mock_agent.config = {"skills": {}}
        mock_agents_loader.return_value.get_agent.return_value = mock_agent

        self.assertEqual(self.loader.get_skills_overview(make_context(agent_id="agent_1")), "")


SKILL_WITH_CORE = """---
name: Planner
description: Plans things.
---
## Core
### Steps
Do the plan in two steps.
End of core: load_skill("planner") for the reference.

## Reference
### Schemas
A very long schema the agent rarely needs.
"""

SKILL_WITHOUT_CORE = """---
name: Plain
description: A skill with no Core section.
---
## Overview
Plain body text.
"""


class TestSkillText(unittest.TestCase):

    def test_body_drops_frontmatter(self):
        self.assertEqual(skill_body(SKILL_WITHOUT_CORE), "## Overview\nPlain body text.")

    def test_body_without_frontmatter_is_the_text(self):
        self.assertEqual(skill_body("Just text.\n"), "Just text.")

    def test_core_is_the_text_between_core_and_reference(self):
        core = core_section(SKILL_WITH_CORE)
        self.assertEqual(core, '### Steps\nDo the plan in two steps.\nEnd of core: load_skill("planner") for the reference.')
        self.assertNotIn("schema", core)

    def test_core_without_reference_runs_to_the_end(self):
        self.assertEqual(core_section("## Core\nAll of it.\n"), "All of it.")

    def test_no_core_section(self):
        self.assertIsNone(core_section(SKILL_WITHOUT_CORE))


class TestPreload(unittest.TestCase):
    """Preloaded skills are inlined into the prompt; real files in a temp skills dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for skill_id, text in (("planner", SKILL_WITH_CORE), ("plain", SKILL_WITHOUT_CORE), ("extra", SKILL_WITHOUT_CORE)):
            os.makedirs(os.path.join(self.tmp.name, skill_id))
            with open(os.path.join(self.tmp.name, skill_id, "skill.json"), "w") as f:
                f.write(f'{{"skill_id": "{skill_id}", "name": "{skill_id.title()}", "description": "About {skill_id}."}}')
            with open(os.path.join(self.tmp.name, skill_id, "SKILL.md"), "w") as f:
                f.write(text)
        SkillsLoader._instance = None
        self.loader = SkillsLoader(skills_dir=self.tmp.name)
        self.addCleanup(setattr, SkillsLoader, "_instance", None)

        agents = patch('core.loaders.agents_loader.AgentsLoader').start()
        self.addCleanup(patch.stopall)
        self.agent = MagicMock()
        agents.return_value.get_agent.return_value = self.agent
        patch('core.loaders.graphs_loader.GraphsLoader.get_graph_skills', return_value=[]).start()
        self.ctx = make_context(agent_id="agent_1")

    def overview(self, skills):
        self.agent.config = {"skills": skills}
        return self.loader.get_skills_overview(self.ctx)

    def test_full_preload_inlines_the_body(self):
        out = self.overview({"plain": {"preload": "full"}})
        self.assertIn("- Plain (id:plain): About plain. [preloaded]", out)
        self.assertIn('<skill id="plain" preload="full">\n## Overview\nPlain body text.\n</skill>', out)
        self.assertNotIn("description: A skill with no Core section.", out)

    def test_core_preload_inlines_only_the_core(self):
        out = self.overview({"planner": {"preload": "core"}})
        self.assertIn('<skill id="planner" preload="core">\n### Steps\nDo the plan in two steps.', out)
        self.assertNotIn("A very long schema", out)

    def test_on_demand_skills_are_listed_not_inlined(self):
        out = self.overview({"planner": {}, "plain": {"preload": "full"}})
        self.assertIn("- Planner (id:planner): About planner.\n", out)
        self.assertNotIn('<skill id="planner"', out)

    def test_core_preload_without_a_core_section_says_to_load_it(self):
        out = self.overview({"plain": {"preload": "core"}})
        self.assertIn('<skill id="plain" preload="core">\n(Not available here; call load_skill("plain")', out)

    def test_load_skill_is_needed_only_while_something_is_left_to_load(self):
        self.agent.config = {"skills": {"plain": {"preload": "full"}, "extra": {"preload": "full"}}}
        self.assertFalse(self.loader.needs_load_skill(self.ctx))
        self.agent.config = {"skills": {"plain": {"preload": "full"}, "planner": {"preload": "core"}}}
        self.assertTrue(self.loader.needs_load_skill(self.ctx), "a core preload's Reference is loaded on demand")
        self.agent.config = {"skills": {"plain": {"preload": "full"}, "extra": {}}}
        self.assertTrue(self.loader.needs_load_skill(self.ctx))
        self.agent.config = {"skills": {}}
        self.assertFalse(self.loader.needs_load_skill(self.ctx))

    def test_graph_granted_skills_are_never_preloaded(self):
        with patch('core.loaders.graphs_loader.GraphsLoader.get_graph_skills', return_value=["extra"]):
            self.agent.config = {"skills": {"plain": {"preload": "full"}}}
            self.assertEqual(self.loader.preload_modes(self.ctx), {"plain": "full"})
            self.assertTrue(self.loader.needs_load_skill(self.ctx))

    def test_edited_skill_text_is_picked_up(self):
        """Only skill.json is hot-reload watched; SKILL.md is re-read on mtime change."""
        self.assertIn("Plain body text.", self.overview({"plain": {"preload": "full"}}))
        path = os.path.join(self.tmp.name, "plain", "SKILL.md")
        with open(path, "w") as f:
            f.write(SKILL_WITHOUT_CORE.replace("Plain body text.", "Edited body."))
        later = time.time() + 5
        os.utime(path, (later, later))
        out = self.overview({"plain": {"preload": "full"}})
        self.assertIn("Edited body.", out)
        self.assertNotIn("Plain body text.", out)

    def test_preloaded_tokens(self):
        self.agent.config = {"skills": {"plain": {"preload": "full"}, "planner": {}}}
        self.assertEqual(self.loader.preloaded_tokens(self.ctx), len("## Overview\nPlain body text.") // 4)


if __name__ == "__main__":
    unittest.main()
