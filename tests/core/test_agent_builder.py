import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.agent_builder import AgentBuilder
from core.agent import Agent

class TestAgentBuilder(unittest.IsolatedAsyncioTestCase):

    @patch('core.mcp_manager.MCPClientManager.get_session')
    @patch('core.agent_builder.load_mcp_tools')
    @patch('core.agent_builder.ChatGoogleGenerativeAI')
    @patch('core.agent_builder.create_react_agent')
    @patch('core.agent_builder.SkillsLoader')
    @patch('core.agent_builder.FlatFileCheckpointer')
    async def test_build_agent_success(self, mock_ff_checkpointer, mock_skills_loader_class, mock_create_react, mock_llm_class, mock_load_tools, mock_get_session):
        # Setup mocks
        mock_loader = MagicMock()
        
        mock_skills_loader = MagicMock()
        mock_skills_loader.load_skills.return_value = "Skill instructions"
        mock_skills_loader_class.return_value = mock_skills_loader
        
        mock_load_tools.return_value = ["tool1", "tool2"]
        mock_llm_class.return_value = MagicMock()
        mock_create_react.return_value = "MockGraph"

        # Mock get_session returns mock session
        mock_session = MagicMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session
        
        builder = AgentBuilder()
        
        # Run
        async with builder.build_agent() as agent:
            # Assertions
            self.assertIsInstance(agent, Agent)
            self.assertEqual(agent.graph, "MockGraph")
        
        mock_get_session.assert_called_once()
        mock_load_tools.assert_called_once_with(mock_session)
        mock_skills_loader.load_skills.assert_called_once()
        mock_create_react.assert_called_once()


if __name__ == "__main__":
    unittest.main()
