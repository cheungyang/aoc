import unittest
from unittest.mock import patch, MagicMock, mock_open
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.loaders.skills_loader import SkillsLoader

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
        mock_agent.config = {"skills": ["dummy_skill"]}
        mock_agents_loader.return_value.get_agent.return_value = mock_agent
        
        skill_content = '{"name": "Dummy", "description": "This is a dummy skill.", "skill_id": "dummy_skill"}'
        
        with patch('core.loaders.skills_loader.open', mock_open(read_data=skill_content)):
            result = self.loader.get_skills_overview('agent_1')

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
        mock_agent.config = {"skills": ["dummy_skill"]}
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
            result = self.loader.get_skill_prompt('agent_1', 'dummy_skill')

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
        mock_agent.config = {"skills": ["skill1"]}
        mock_agents_loader.return_value.get_agent.return_value = mock_agent
        
        skill_content = '{"name": "Dummy", "description": "Desc", "skill_id": "skill1"}'
        
        with patch('core.loaders.skills_loader.open', mock_open(read_data=skill_content)):
            result = self.loader.get_skills_overview('agent_1')

        self.assertIn("- Dummy (id:skill1): Desc", result)

if __name__ == "__main__":
    unittest.main()
