import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.agent_list import agent_list
from core.util import format_tool_response

class TestAgentListTool(unittest.TestCase):

    @patch('tools.agent_list.AgentsLoader')
    def test_agent_list(self, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        
        # Mock agent IDs
        mock_loader.list_agent_ids.return_value = ["main", "designer"]
        
        # Mock get_agent
        def get_agent_mock(agent_id):
            m = MagicMock()
            if agent_id == "main":
                m.config = {"name": "Concierge", "emoji": "🛎️", "description": "Helper", "extra": "secret"}
            elif agent_id == "designer":
                m.config = {"name": "Aki", "emoji": "🤖", "description": "Designer"}
            return m
        mock_loader.get_agent.side_effect = get_agent_mock
        
        result = agent_list.func()
        
        expected_payload = [
            {"agent_id": "main", "name": "Concierge", "emoji": "🛎️", "description": "Helper"},
            {"agent_id": "designer", "name": "Aki", "emoji": "🤖", "description": "Designer"}
        ]
        self.assertEqual(result, format_tool_response("agent_list", payload=str(expected_payload), errors="None"))

if __name__ == '__main__':
    unittest.main()
