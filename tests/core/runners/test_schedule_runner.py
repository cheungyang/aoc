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
    async def test_schedule_loading_array_prompt(self, mock_croniter, mock_bots_loader, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.list_agent_ids.return_value = ["agent1"]
        
        mock_agent = MagicMock()
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.config = {
            "schedules": [
                {"cron": "* * * * *", "prompt": ["line1", "line2"], "enabled": "true", "channel": "test-channel"}
            ]
        }
        
        mock_iter = MagicMock()
        mock_croniter.return_value = mock_iter
        mock_iter.get_next.return_value = datetime.datetime.now() + datetime.timedelta(minutes=1)
        
        runner = ScheduleRunner()
        
        self.assertEqual(len(runner.schedules), 1)
        self.assertEqual(runner.schedules[0]["prompt"], "line1\nline2")

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
            
        mock_agent.execute.assert_called_once_with("test prompt", channel=mock_channel, role="user", source="scheduled")

    @patch('core.runners.schedule_runner.AgentsLoader')
    @patch('core.runners.schedule_runner.BotsLoader')
    @patch('core.runners.schedule_runner.croniter')
    async def test_execute_schedule_with_thread(self, mock_croniter, mock_bots_loader, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.list_agent_ids.return_value = ["agent1"]
        
        mock_agent = MagicMock()
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.config = {
            "schedules": [
                {"cron": "* * * * *", "prompt": "test prompt", "enabled": "true", "channel": "test-channel", "thread": "test-thread"}
            ],
            "channel_hosts": ["test-channel"]
        }
        mock_agent.get_config = MagicMock(side_effect=lambda key, default=None: mock_agent.config.get(key, default))
        mock_agent.execute = AsyncMock(return_value="agent response")
        
        mock_bots = MagicMock()
        mock_bots_loader.return_value = mock_bots
        mock_channel = MagicMock()
        mock_channel.name = "test-channel"
        mock_bots.get_channel.return_value = mock_channel
        
        mock_thread = MagicMock()
        mock_thread.name = "test-thread"
        mock_channel.threads = [mock_thread]
        
        mock_iter = MagicMock()
        mock_croniter.return_value = mock_iter
        mock_iter.get_next.return_value = datetime.datetime.now() + datetime.timedelta(minutes=1)
        
        runner = ScheduleRunner()
        
        await runner._execute_schedule(runner.schedules[0])
            
        mock_agent.execute.assert_called_once_with("test prompt", channel=mock_thread, role="user", source="scheduled")

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
            
        mock_agent.execute.assert_called_once_with("test prompt", channel=mock_channel, role="user", source="scheduled")

    @patch('core.runners.schedule_runner.AgentsLoader')
    @patch('core.runners.schedule_runner.BotsLoader')
    @patch('core.runners.schedule_runner.croniter')
    async def test_execute_schedule_passes_channel_name_to_get_channel(self, mock_croniter, mock_bots_loader, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.list_agent_ids.return_value = ["main", "day-planner"]
        
        mock_main_agent = MagicMock()
        mock_main_agent.config = {
            "channel_hosts": ["general", "day-planning"]
        }
        mock_main_agent.get_config = MagicMock(side_effect=lambda key, default=None: mock_main_agent.config.get(key, default))
        
        mock_day_planner = MagicMock()
        mock_day_planner.config = {
            "schedules": [
                {"cron": "0 6 * * *", "prompt": "Provide your daily update", "enabled": "true", "channel": "day-planning"}
            ]
        }
        mock_day_planner.get_config = MagicMock(side_effect=lambda key, default=None: mock_day_planner.config.get(key, default))
        mock_day_planner.execute = AsyncMock(return_value="response")
        
        mock_loader.get_agent.side_effect = lambda aid: mock_main_agent if aid == "main" else mock_day_planner
        
        mock_bots = MagicMock()
        mock_bots_loader.return_value = mock_bots
        
        mock_target_channel = MagicMock()
        mock_target_channel.name = "day-planning"
        mock_bots.get_channel.return_value = mock_target_channel
        
        mock_iter = MagicMock()
        mock_croniter.return_value = mock_iter
        mock_iter.get_next.return_value = datetime.datetime.now() + datetime.timedelta(minutes=1)
        
        runner = ScheduleRunner()
        await runner._execute_schedule(runner.schedules[0])
        
        mock_bots.get_channel.assert_called_once_with("main", "day-planning")
        mock_day_planner.execute.assert_called_once_with("Provide your daily update", channel=mock_target_channel, role="user", source="scheduled")

    @patch('core.runners.schedule_runner.AgentsLoader')
    @patch('core.runners.schedule_runner.BotsLoader')
    @patch('core.runners.schedule_runner.croniter')
    @patch('core.runners.schedule_runner.asyncio.create_task')
    @patch('asyncio.sleep')
    async def test_start_spawns_async_tasks_for_schedules(self, mock_sleep, mock_create_task, mock_croniter, mock_bots_loader, mock_agents_loader):
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
        past_time = datetime.datetime.now() - datetime.timedelta(minutes=1)
        future_time = datetime.datetime.now() + datetime.timedelta(minutes=1)
        mock_iter.get_next.return_value = future_time
        
        runner = ScheduleRunner()
        runner.schedules[0]["next_run"] = past_time
        
        import asyncio
        mock_sleep.side_effect = [None, asyncio.CancelledError()]
        
        try:
            await runner.start()
        except asyncio.CancelledError:
            pass
            
        mock_create_task.assert_called_once()
        coro = mock_create_task.call_args[0][0]
        coro.close()
        self.assertEqual(runner.schedules[0]["next_run"], future_time)

    @patch('core.runners.schedule_runner.AgentsLoader')
    @patch('core.runners.schedule_runner.BotsLoader')
    @patch('core.runners.schedule_runner.croniter')
    @patch('core.runners.schedule_runner.Config')
    async def test_debug_mode_skips_unallowed_channels(self, mock_config_class, mock_croniter, mock_bots_loader, mock_agents_loader):
        mock_config = MagicMock()
        mock_config.is_channel_allowed.return_value = False
        mock_config.debug_channel = "debug-only"
        mock_config_class.return_value = mock_config
        
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.list_agent_ids.return_value = ["agent1"]
        
        mock_agent = MagicMock()
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.config = {
            "schedules": [
                {"cron": "* * * * *", "prompt": "test prompt", "enabled": "true", "channel": "other-channel"}
            ]
        }
        mock_agent.execute = AsyncMock()
        
        mock_iter = MagicMock()
        mock_croniter.return_value = mock_iter
        mock_iter.get_next.return_value = datetime.datetime.now() + datetime.timedelta(minutes=1)
        
        runner = ScheduleRunner()
        await runner._execute_schedule(runner.schedules[0])
        
        mock_agent.execute.assert_not_called()

if __name__ == '__main__':
    unittest.main()
