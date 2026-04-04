import unittest
from unittest.mock import patch, MagicMock, mock_open
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.skills_loader import SkillsLoader

class TestSkillsLoader(unittest.TestCase):

    def setUp(self):
        SkillsLoader._instance = None
        self.loader = SkillsLoader()

    @patch('core.skills_loader.os.path.isdir')
    @patch('core.skills_loader.os.listdir')
    @patch('core.skills_loader.os.path.isfile')
    def test_load_skills_success(self, mock_isfile, mock_listdir, mock_isdir):
        # Setup mocks
        mock_isdir.return_value = True
        mock_listdir.return_value = ['dummy_skill']
        mock_isfile.return_value = True
        
        skill_content = """---
name: Dummy
---
Body content of skill.
"""
        
        with patch('core.skills_loader.open', mock_open(read_data=skill_content)):
            result = self.loader.get_skill_prompt()

        self.assertIn("### Skill: dummy_skill", result)
        self.assertIn("Body content of skill.", result)

    @patch('core.skills_loader.os.path.isdir')
    @patch('core.skills_loader.os.listdir')
    @patch('core.skills_loader.os.path.isfile')
    def test_load_skills_filtered(self, mock_isfile, mock_listdir, mock_isdir):
        # Setup mocks
        mock_isdir.return_value = True
        mock_listdir.return_value = ['skill1', 'skill2']
        mock_isfile.return_value = True
        
        skill_content = """---
name: Dummy
---
Body content of skill.
"""
        
        with patch('core.skills_loader.open', mock_open(read_data=skill_content)):
            # Only allow skill1
            result = self.loader.get_skill_prompt(allowed_skills=['skill1'])

        self.assertIn("### Skill: skill1", result)
        self.assertNotIn("### Skill: skill2", result)

if __name__ == "__main__":
    unittest.main()
