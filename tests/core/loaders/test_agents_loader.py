import unittest
from unittest.mock import patch, mock_open, MagicMock, AsyncMock
import os
import json
from core.loaders.agents_loader import AgentsLoader

class TestAgentsLoader(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Reset the singleton instance before each test
        AgentsLoader._instance = None
        AgentsLoader._agents = None

    @patch('os.path.exists')
    @patch('os.listdir')
    @patch('os.path.isdir')
    @patch('builtins.open', new_callable=mock_open)
    def test_load_agents(self, mock_file, mock_isdir, mock_listdir, mock_exists):
        mock_exists.side_effect = lambda path: True if 'agent.json' in path or 'agents' in path else False
        mock_listdir.return_value = ['agent1', 'agent2']
        mock_isdir.return_value = True
        
        # Simulate different json content for each file
        file_contents = [
            json.dumps({"name": "Agent 1", "discord_token_key": "TOKEN_1"}),
            json.dumps({"id": "custom-id", "name": "Agent 2"})
        ]
        # mock_open(read_data=...) returns a mock that yields the data.
        # We need to provide a side_effect that returns these mocks.
        mocks = [mock_open(read_data=c).return_value for c in file_contents]
        mock_file.side_effect = mocks

        loader = AgentsLoader()
        agents = loader.list_agent_ids()

        self.assertEqual(len(agents), 2)
        
        # Verify IDs
        self.assertIn('agent1', agents)
        self.assertIn('custom-id', agents)
        
        # Verify configs
        config1 = loader.get_agent('agent1').config
        self.assertEqual(config1['name'], 'Agent 1')
        
        config2 = loader.get_agent('custom-id').config
        self.assertEqual(config2['name'], 'Agent 2')

    def test_singleton(self):
        loader1 = AgentsLoader()
        loader2 = AgentsLoader()
        self.assertIs(loader1, loader2)

    @patch('os.path.exists')
    def test_load_agents_no_dir(self, mock_exists):
        mock_exists.return_value = False
        loader = AgentsLoader()
        self.assertEqual(loader.list_agent_ids(), [])

    async def test_get_agent_caching(self):
        loader = AgentsLoader()
        loader._agent_configs = {"test-agent": {"id": "test-agent"}}

        agent1 = loader.get_agent("test-agent")
        agent2 = loader.get_agent("test-agent")
        
        self.assertIs(agent1, agent2)





if __name__ == '__main__':
    unittest.main()
