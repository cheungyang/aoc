import unittest
from unittest.mock import patch, mock_open
import os
import json
from core.agents_loader import AgentsLoader

class TestAgentsLoader(unittest.TestCase):

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
        agents = loader.list_agents()

        self.assertEqual(len(agents), 2)
        
        # Verify Agent 1 (default ID from folder name)
        self.assertEqual(agents[0]['id'], 'agent1')
        self.assertEqual(agents[0]['name'], 'Agent 1')
        
        # Verify Agent 2 (custom ID from json)
        self.assertEqual(agents[1]['id'], 'custom-id')
        self.assertEqual(agents[1]['name'], 'Agent 2')

    def test_singleton(self):
        loader1 = AgentsLoader()
        loader2 = AgentsLoader()
        self.assertIs(loader1, loader2)

    @patch('os.path.exists')
    def test_load_agents_no_dir(self, mock_exists):
        mock_exists.return_value = False
        loader = AgentsLoader()
        self.assertEqual(loader.list_agents(), [])

if __name__ == '__main__':
    unittest.main()
