import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import asyncio
import main

class TestMain(unittest.IsolatedAsyncioTestCase):

    @patch('main.AgentsLoader')
    @patch('main.BotRunner')
    @patch('os.getenv')
    async def test_run_bots(self, mock_getenv, mock_bot_runner, mock_agents_loader):
        # Mock AgentsLoader instance
        mock_loader_instance = MagicMock()
        mock_agents_loader.return_value = mock_loader_instance
        
        # Mock agents list
        mock_loader_instance.list_agents.return_value = [
            {"id": "agent1", "discord_token_key": "TOKEN_1"},
            {"id": "agent2", "discord_token_key": "TOKEN_2"},
            {"id": "agent3"} # No token key
        ]
        
        # Mock getenv
        def side_effect(key):
            if key == "TOKEN_1":
                return "valid_token_1"
            if key == "TOKEN_2":
                return None # Missing token
            return None
        mock_getenv.side_effect = side_effect
        
        # Mock BotRunner
        mock_runner_instance = MagicMock()
        mock_runner_instance.run_bot = AsyncMock()
        mock_bot_runner.return_value = mock_runner_instance

        await main.run_bots()

        # Should create BotRunner only for agent1
        mock_bot_runner.assert_called_once_with("valid_token_1", "agent1")
        mock_runner_instance.run_bot.assert_awaited_once()

    @patch('main.AgentsLoader')
    @patch('os.getenv')
    async def test_run_bots_no_agents(self, mock_getenv, mock_agents_loader):
        mock_loader_instance = MagicMock()
        mock_agents_loader.return_value = mock_loader_instance
        mock_loader_instance.list_agents.return_value = []

        await main.run_bots()
        # Should just print "No Discord bots to start."
        
if __name__ == '__main__':
    unittest.main()
