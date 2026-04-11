import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import datetime
import sys
import os

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.runners.schedule_runner import ScheduleRunner

class TestScheduleRunner(unittest.IsolatedAsyncioTestCase):

    @patch('core.runners.schedule_runner.AgentsLoader')
    @patch('core.runners.schedule_runner.BotsLoader')
    @patch('core.runners.schedule_runner.croniter')
    async def test_schedule_loading(self, mock_croniter, mock_bots_loader, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.list_agent_ids.return_value = ["agent1"]
        
        mock_agent = MagicMock()
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.config = {
            "schedules": [
                {"cron": "* * * * *", "prompt": "test prompt", "enabled": "true", "channel": "test-channel"}
            ]
        }
        
        mock_iter = MagicMock()
        mock_croniter.return_value = mock_iter
        mock_iter.get_next.return_value = datetime.datetime.now() + datetime.timedelta(minutes=1)
        
        runner = ScheduleRunner()
        
        self.assertEqual(len(runner.schedules), 1)
        self.assertEqual(runner.schedules[0]["agent_id"], "agent1")
        self.assertEqual(runner.schedules[0]["cron"], "* * * * *")

    @patch('core.runners.schedule_runner.AgentsLoader')
    @patch('core.runners.schedule_runner.BotsLoader')
    @patch('core.runners.schedule_runner.croniter')
    async def test_execute_schedule(self, mock_croniter, mock_bots_loader, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.list_agent_ids.return_value = ["agent1"]
        
        mock_agent = MagicMock()
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.config = {
            "schedules": [
                {"cron": "* * * * *", "prompt": "test prompt", "enabled": "true", "channel": "test-channel"}
            ],
            "channel_hosts": ["test-channel"]
        }
        mock_agent.get_config = MagicMock(side_effect=lambda key, default=None: mock_agent.config.get(key, default))
        mock_agent.execute = AsyncMock(return_value="agent response")
        
        mock_bots = MagicMock()
        mock_bots_loader.return_value = mock_bots
        mock_bot_runner = MagicMock()
        mock_bots.get_bot.return_value = mock_bot_runner
        mock_bot = MagicMock()
        mock_bot_runner.bot = mock_bot
        
        # Mock guild and channel
        mock_guild = MagicMock()
        mock_channel = MagicMock()
        mock_channel.name = "test-channel"
        mock_guild.text_channels = [mock_channel]
        mock_bot.guilds = [mock_guild]
        mock_channel.send = AsyncMock()
        mock_bots.get_channel.return_value = mock_channel
        
        mock_iter = MagicMock()
        mock_croniter.return_value = mock_iter
        mock_iter.get_next.return_value = datetime.datetime.now() + datetime.timedelta(minutes=1)
        
        runner = ScheduleRunner()
        
        # Test _execute_schedule directly
        await runner._execute_schedule(runner.schedules[0])
            
        mock_agent.execute.assert_called_once_with("test prompt", channel=mock_channel, role="system", source="scheduled")

    @patch('core.runners.schedule_runner.AgentsLoader')
    @patch('core.runners.schedule_runner.BotsLoader')
    @patch('core.runners.schedule_runner.croniter')
    async def test_execute_schedule_long_message(self, mock_croniter, mock_bots_loader, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.list_agent_ids.return_value = ["agent1"]
        
        mock_agent = MagicMock()
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.config = {
            "schedules": [
                {"cron": "* * * * *", "prompt": "test prompt", "enabled": "true", "channel": "test-channel"}
            ],
            "channel_hosts": ["test-channel"]
        }
        mock_agent.get_config = MagicMock(side_effect=lambda key, default=None: mock_agent.config.get(key, default))
        # Return a long string (4500 chars)
        long_response = "a" * 4500
        mock_agent.execute = AsyncMock(return_value=long_response)
        
        mock_bots = MagicMock()
        mock_bots_loader.return_value = mock_bots
        mock_bot_runner = MagicMock()
        mock_bots.get_bot.return_value = mock_bot_runner
        mock_bot = MagicMock()
        mock_bot_runner.bot = mock_bot
        
        mock_guild = MagicMock()
        mock_channel = MagicMock()
        mock_channel.name = "test-channel"
        mock_guild.text_channels = [mock_channel]
        mock_bot.guilds = [mock_guild]
        mock_channel.send = AsyncMock()
        mock_bots.get_channel.return_value = mock_channel
        
        mock_iter = MagicMock()
        mock_croniter.return_value = mock_iter
        mock_iter.get_next.return_value = datetime.datetime.now() + datetime.timedelta(minutes=1)
        
        runner = ScheduleRunner()
        
        await runner._execute_schedule(runner.schedules[0])
            
        mock_agent.execute.assert_called_once_with("test prompt", channel=mock_channel, role="system", source="scheduled")

if __name__ == '__main__':
    unittest.main()
