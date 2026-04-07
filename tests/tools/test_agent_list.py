import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.agent_list import agent_list

class TestAgentListTool(unittest.TestCase):

    @patch('tools.agent_list.AgentsLoader')
    def test_agent_list(self, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        
        # Mock agents list with mixed data
        mock_loader.list_agents.return_value = [
            {"agent_id": "main", "name": "Concierge", "emoji": "🛎️", "description": "Helper", "extra": "secret"},
            {"id": "designer", "name": "Aki", "emoji": "🤖", "description": "Designer"}
        ]
        
        result = agent_list.func()
        
        self.assertEqual(len(result), 2)
        
        # Verify first agent
        self.assertEqual(result[0]["agent_id"], "main")
        self.assertEqual(result[0]["name"], "Concierge")
        self.assertNotIn("extra", result[0])
        
        # Verify second agent (mapped id to agent_id)
        self.assertEqual(result[1]["agent_id"], "designer")
        self.assertEqual(result[1]["name"], "Aki")

if __name__ == '__main__':
    unittest.main()
