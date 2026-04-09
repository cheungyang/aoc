import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.agent import Agent

class TestAgentGraphBuilding(unittest.IsolatedAsyncioTestCase):
 
     def setUp(self):
          from core.loaders.hooks_loader import HooksLoader
          HooksLoader._instance = None
 
     @patch('core.agent.graph_builder.AgentsLoader')
     @patch('core.agent.graph_builder.SkillsLoader')
     @patch('core.agent.graph_builder.ToolsLoader')
     @patch('core.agent.graph_builder.ChatGoogleGenerativeAI')
     @patch('core.agent.graph_builder.create_react_agent')
     @patch('core.agent.graph_builder.FlatFileCheckpointer')
     async def test_build_graph_success(self, mock_ff_checkpointer, mock_create_react, mock_llm_class, mock_tool_loader_class, mock_skills_loader_class, mock_agents_loader_class):
         # Setup mocks
         mock_llm_class.return_value = MagicMock()
         mock_create_react.return_value = "MockGraph"
         
         mock_agents_loader = MagicMock()
         mock_agents_loader_class.return_value = mock_agents_loader
         mock_agents_loader.get_agent_prompt.return_value = "Mock Agent Prompt"
         
         mock_tool1 = MagicMock()
         mock_tool1.name = "tool1"
         mock_loader = MagicMock()
         mock_tool_loader_class.return_value = mock_loader
         mock_loader.get_tools.return_value = [mock_tool1]
         
         mock_skills_loader = MagicMock()
         mock_skills_loader_class.return_value = mock_skills_loader
         mock_skills_loader.get_skills_overview.return_value = "Mock Skills"
         
         agent = Agent("main", config={"tools": {"tool1": {}}})
         
         # Run
         graph = await agent._build_graph()
         
         # Assertions
         self.assertEqual(graph, "MockGraph")
         mock_create_react.assert_called_once_with(mock_llm_class.return_value, [mock_tool1], prompt=unittest.mock.ANY, checkpointer=mock_ff_checkpointer.return_value)
 
     @patch('core.agent.graph_builder.AgentsLoader')
     @patch('core.agent.graph_builder.SkillsLoader')
     @patch('core.agent.graph_builder.ToolsLoader')
     @patch('core.agent.graph_builder.ChatGoogleGenerativeAI')
     @patch('core.agent.graph_builder.create_react_agent')
     @patch('core.agent.graph_builder.FlatFileCheckpointer')
     async def test_build_graph_filtering(self, mock_ff_checkpointer, mock_create_react, mock_llm_class, mock_tool_loader_class, mock_skills_loader_class, mock_agents_loader_class):
         # Setup mocks
         mock_loader = MagicMock()
         mock_agents_loader_class.return_value = mock_loader
         mock_loader.get_agent_prompt.return_value = "Mock Agent Prompt"
 
         mock_tool1 = MagicMock()
         mock_tool1.name = "tool1"
         mock_tool_loader = MagicMock()
         mock_tool_loader_class.return_value = mock_tool_loader
         mock_tool_loader.get_tools.return_value = [mock_tool1]
         
         mock_skills_loader = MagicMock()
         mock_skills_loader_class.return_value = mock_skills_loader
         mock_skills_loader.get_skills_overview.return_value = "Mock Skills"
         
         agent = Agent("test_agent", config={"tools": {"tool1": {}}, "skills": ["skill1"]})
 
         graph = await agent._build_graph()
 
         mock_create_react.assert_called_once_with(
             unittest.mock.ANY, 
             [mock_tool1],
             prompt=unittest.mock.ANY, 
             checkpointer=unittest.mock.ANY
         )

            

if __name__ == "__main__":
    unittest.main()
