import unittest
from unittest.mock import patch, mock_open, MagicMock, AsyncMock
import os
import json
from core.agent.agents_loader import AgentsLoader

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

    @patch('core.agent.agents_loader.AgentBuilder')
    @patch('core.agent.agents_loader.AgentsLoader.get_agent_config')
    async def test_get_agent_caching(self, mock_get_config, mock_agent_builder_class):
        mock_get_config.return_value = {"id": "test-agent"}
        mock_builder = MagicMock()
        mock_agent_builder_class.return_value = mock_builder
        mock_agent = MagicMock()
        mock_builder.build_agent = AsyncMock(return_value=mock_agent)

        loader = AgentsLoader()
        agent1 = await loader.get_agent("test-agent")
        agent2 = await loader.get_agent("test-agent")
        
        self.assertIs(agent1, agent2)
        mock_builder.build_agent.assert_called_once()

    @patch('core.agent.agents_loader.AgentBuilder')
    @patch('core.agent.agents_loader.AgentsLoader.get_agent_config')
    @patch('os.path.exists')
    @patch('os.walk')
    @patch('os.path.getmtime')
    async def test_get_agent_hot_reload(self, mock_getmtime, mock_walk, mock_exists, mock_get_config, mock_agent_builder_class):
        mock_get_config.return_value = {"id": "test-agent"}
        mock_builder = MagicMock()
        mock_agent_builder_class.return_value = mock_builder
        mock_agent1 = MagicMock()
        mock_agent2 = MagicMock()
        mock_builder.build_agent = AsyncMock(side_effect=[mock_agent1, mock_agent2])

        mock_exists.return_value = True
        mock_walk.return_value = [('/path/to/agents', (), ('agent.json',))]
        
        import time
        current_time = time.time()
        
        loader = AgentsLoader()
        loader._last_loaded_time = current_time - 10
        
        mock_getmtime.return_value = current_time
        
        agent1 = await loader.get_agent("test-agent")
        
        # Force reload by making files newer
        mock_getmtime.return_value = current_time + 10
        
        agent2 = await loader.get_agent("test-agent")
        
        self.assertIsNot(agent1, agent2)
        self.assertEqual(mock_builder.build_agent.call_count, 2)

if __name__ == '__main__':
    unittest.main()
