import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.agent_builder import AgentBuilder
from core.agent import Agent

class TestAgentBuilder(unittest.TestCase):

    @patch('core.agent_builder.ChatGoogleGenerativeAI')
    @patch('core.agent_builder.create_react_agent')
    @patch('core.agent_builder.SkillsLoader')
    @patch('core.agent_builder.FlatFileCheckpointer')
    def test_build_agent_success(self, mock_ff_checkpointer, mock_skills_loader_class, mock_create_react, mock_llm_class):
        # Setup mocks
        
        mock_skills_loader = MagicMock()
        mock_skills_loader.load_skills.return_value = "Skill instructions"
        mock_skills_loader_class.return_value = mock_skills_loader
        
        mock_llm_class.return_value = MagicMock()
        mock_create_react.return_value = "MockGraph"
        
        mock_tools = ["tool1", "tool2"]
        builder = AgentBuilder(mock_tools)
        
        # Run
        agent = builder.build_agent()
        
        # Assertions
        self.assertIsInstance(agent, Agent)
        self.assertEqual(agent.graph, "MockGraph")
        
        mock_skills_loader.load_skills.assert_called_once()
        mock_create_react.assert_called_once_with(mock_llm_class.return_value, mock_tools, prompt=unittest.mock.ANY, checkpointer=mock_ff_checkpointer.return_value)


if __name__ == "__main__":
    unittest.main()
