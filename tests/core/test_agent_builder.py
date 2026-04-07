import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.agent_builder import AgentBuilder
from core.agent import Agent

class TestAgentBuilder(unittest.IsolatedAsyncioTestCase):
 
     def setUp(self):
         from core.hook_loader import HookLoader
         HookLoader._instance = None
 
     @patch('core.agent_builder.load_mcp_tools')
     @patch('core.agent_builder.ChatGoogleGenerativeAI')
     @patch('core.agent_builder.create_react_agent')
     @patch('core.agent_builder.FlatFileCheckpointer')
     async def test_build_agent_success(self, mock_ff_checkpointer, mock_create_react, mock_llm_class, mock_load_tools):
         # Setup mocks
         mock_llm_class.return_value = MagicMock()
         mock_create_react.return_value = "MockGraph"
         
         mock_tool1 = MagicMock()
         mock_tool1.name = "tool1"
         mock_load_tools.return_value = [mock_tool1]
         
         mock_session = AsyncMock()
         
         builder = AgentBuilder(mock_session)
         
         # Run
         agent = await builder.build_agent()
         
         # Assertions
         self.assertIsInstance(agent, Agent)
         self.assertEqual(agent.graph, "MockGraph")
         
         mock_session.call_tool.assert_called_once_with("configure_session", arguments={"allowed_tools": unittest.mock.ANY})
         mock_create_react.assert_called_once_with(mock_llm_class.return_value, [mock_tool1], prompt=unittest.mock.ANY, checkpointer=mock_ff_checkpointer.return_value)
 

 
     @patch('core.agent_builder.load_mcp_tools')
     @patch('core.agent_builder.ChatGoogleGenerativeAI')
     @patch('core.agent_builder.create_react_agent')
     @patch('core.agent_builder.FlatFileCheckpointer')
     @patch('os.listdir')
     @patch('os.path.isdir')
     @patch('os.path.exists')
     async def test_build_agent_filtering(self, mock_exists, mock_isdir, mock_listdir, mock_ff_checkpointer, mock_create_react, mock_llm_class, mock_load_tools):
         # Setup mocks
         mock_isdir.return_value = True
         mock_exists.return_value = True
         mock_listdir.return_value = ["SOUL.md", "IDENTITY.md"]
 
         config_json = '{"name": "TestAgent", "tools": ["tool1"], "skills": ["skill1"]}'
         
         mock_open = unittest.mock.mock_open()
         mock_open.side_effect = [
             unittest.mock.mock_open(read_data=config_json).return_value,
             unittest.mock.mock_open(read_data="# SOUL").return_value,
             unittest.mock.mock_open(read_data="# IDENTITY").return_value
         ]
 
         with patch('builtins.open', mock_open):
             mock_tool1 = MagicMock()
             mock_tool1.name = "tool1"
             mock_load_tools.return_value = [mock_tool1]
             
             mock_session = AsyncMock()
             builder = AgentBuilder(mock_session)
 
             agent = await builder.build_agent("test_agent")
 
             mock_session.call_tool.assert_called_once_with("configure_session", arguments={"allowed_tools": ["tool1"]})
             mock_create_react.assert_called_once_with(
                 unittest.mock.ANY, 
                 [mock_tool1],
                 prompt=unittest.mock.ANY, 
                 checkpointer=unittest.mock.ANY
             )

            

if __name__ == "__main__":
    unittest.main()
