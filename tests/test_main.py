import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys
import asyncio

# Provide mock for discord if not installed
if 'discord' not in sys.modules:
    mock_discord = MagicMock()
    mock_discord.Thread = type('Thread', (), {})
    sys.modules['discord'] = mock_discord
    sys.modules['discord.ext'] = MagicMock()
    sys.modules['discord.ext.commands'] = MagicMock()
    sys.modules['discord.ui'] = MagicMock()
    sys.modules['mcp'] = MagicMock()
    sys.modules['mcp.client'] = MagicMock()
    sys.modules['mcp.client.stdio'] = MagicMock()
    sys.modules['langchain_mcp_adapters'] = MagicMock()
    sys.modules['langchain_mcp_adapters.tools'] = MagicMock()
    sys.modules['croniter'] = MagicMock()

import main
from core.config import Config

class TestMain(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.config = Config()
        self.config.reset()

    def tearDown(self):
        self.config.reset()

    @patch('main.ScheduleRunner')
    @patch('main.AgentsLoader')
    @patch('main.BotsLoader')
    async def test_run_bots(self, mock_bots_loader_class, mock_agents_loader, mock_schedule_runner_class):
        # Mock ScheduleRunner
        mock_schedule_runner = MagicMock()
        mock_schedule_runner.start = AsyncMock()
        mock_schedule_runner_class.return_value = mock_schedule_runner
        
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

    @patch('main.ScheduleRunner')
    @patch('main.AgentsLoader')
    @patch('main.BotsLoader')
    async def test_run_bots_no_agents(self, mock_bots_loader, mock_agents_loader, mock_schedule_runner_class):
        # Mock ScheduleRunner
        mock_schedule_runner = MagicMock()
        mock_schedule_runner.start = AsyncMock()
        mock_schedule_runner_class.return_value = mock_schedule_runner
        
        mock_loader_instance = MagicMock()
        mock_agents_loader.return_value = mock_loader_instance
        mock_loader_instance.list_agent_ids.return_value = []

        await main.run_bots()
        # Should just print "No Discord bots to start."

    def test_parse_args_default(self):
        args = main.parse_args([])
        self.assertFalse(args.debug)
        self.assertIsNone(args.debug_channel)

    def test_parse_args_debug_flag(self):
        args = main.parse_args(["--debug"])
        self.assertTrue(args.debug)
        self.assertIsNone(args.debug_channel)

    def test_parse_args_debug_channel(self):
        args = main.parse_args(["--debug", "--debug-channel", "test-chan"])
        self.assertTrue(args.debug)
        self.assertEqual(args.debug_channel, "test-chan")

    @patch('main.ScheduleRunner')
    @patch('main.AgentsLoader')
    @patch('main.BotsLoader')
    async def test_run_bots_with_debug_param(self, mock_bots_loader, mock_agents_loader, mock_schedule_runner_class):
        mock_schedule_runner = MagicMock()
        mock_schedule_runner.start = AsyncMock()
        mock_schedule_runner_class.return_value = mock_schedule_runner
        
        mock_loader_instance = MagicMock()
        mock_agents_loader.return_value = mock_loader_instance
        mock_loader_instance.list_agent_ids.return_value = []

        await main.run_bots(is_debug=True, debug_channel="debug-room")
        self.assertTrue(Config().is_debug)
        self.assertEqual(Config().debug_channel, "debug-room")


if __name__ == '__main__':
    unittest.main()
