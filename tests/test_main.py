import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import asyncio
import main

class TestMain(unittest.IsolatedAsyncioTestCase):

    @patch('main.AgentsLoader')
    @patch('main.BotsLoader')
    async def test_run_bots(self, mock_bots_loader_class, mock_agents_loader):
        # Mock AgentsLoader instance
        mock_loader_instance = MagicMock()
        mock_agents_loader.return_value = mock_loader_instance
        
        # Mock agent IDs
        mock_loader_instance.list_agent_ids.return_value = ["agent1", "agent2", "agent3"]
        
        # Mock BotsLoader
        mock_bots_loader = MagicMock()
        mock_bots_loader_class.return_value = mock_bots_loader
        
        mock_runner_instance = MagicMock()
        mock_runner_instance.run_bot = AsyncMock()
        
        def get_bot_mock(agent_id):
            if agent_id == "agent1":
                return mock_runner_instance
            return None
        mock_bots_loader.get_bot.side_effect = get_bot_mock

        await main.run_bots()

        # Should call run_bot only for agent1
        mock_runner_instance.run_bot.assert_awaited_once()

    @patch('main.AgentsLoader')
    @patch('main.BotsLoader')
    async def test_run_bots_no_agents(self, mock_bots_loader, mock_agents_loader):
        mock_loader_instance = MagicMock()
        mock_agents_loader.return_value = mock_loader_instance
        mock_loader_instance.list_agent_ids.return_value = []

        await main.run_bots()
        # Should just print "No Discord bots to start."
        
if __name__ == '__main__':
    unittest.main()
