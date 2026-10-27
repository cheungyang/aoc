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
    sys.modules['croniter'] = MagicMock()

import main
from core.config.settings import Config

class TestMain(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.config = Config()
        self.config.reset()
        # The proactive team always runs next to the bots; its start() never
        # returns, and install() hooks into the whole process. Stood in for.
        proactive = patch('main.install_proactive')
        self.install_proactive = proactive.start()
        self.install_proactive.return_value.start = AsyncMock()
        self.addCleanup(proactive.stop)

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
        # The proactive team runs next to the bots; its deliveries run on the
        # schedule runner, which a `now` delivery wakes, and its `next` routes
        # ride along on the runner's scheduled runs.
        self.install_proactive.assert_called_once_with(wake=mock_schedule_runner.wake)
        mock_schedule_runner.add_due_work.assert_called_once_with(self.install_proactive.return_value.due_work)
        mock_schedule_runner.add_briefing.assert_called_once_with(self.install_proactive.return_value.briefing)
        self.install_proactive.return_value.start.assert_awaited_once()

    @patch('main.ScheduleRunner')
    @patch('main.AgentsLoader')
    @patch('main.BotsLoader')
    async def test_run_bots_no_agents(self, mock_bots_loader, mock_agents_loader, mock_schedule_runner_class):
        """With no agents configured the process must still run the scheduler.

        Scheduled work (cron jobs, ticks) is independent of Discord: an early
        return on "no bots" would silently stop every scheduled task on a
        deployment that has none. The `else: print("No Discord bots to start.")`
        branch in `main.run_bots` is now unreachable precisely because the
        schedule runner is always appended to `tasks`.
        """
        # Mock ScheduleRunner
        mock_schedule_runner = MagicMock()
        mock_schedule_runner.start = AsyncMock()
        mock_schedule_runner_class.return_value = mock_schedule_runner
        
        mock_loader_instance = MagicMock()
        mock_agents_loader.return_value = mock_loader_instance
        mock_loader_instance.list_agent_ids.return_value = []

        mock_bots_loader_instance = MagicMock()
        mock_bots_loader.return_value = mock_bots_loader_instance

        await main.run_bots()

        mock_schedule_runner.start.assert_awaited_once()
        mock_bots_loader_instance.get_bot.assert_not_called()

    def test_parse_args_default(self):
        args = main.parse_args([])
        self.assertFalse(args.dev)
        self.assertEqual(main._split_agents(args.agents), [])

    def test_parse_args_dev_agents(self):
        args = main.parse_args(["--dev", "--agents", "day-planner, main,"])
        self.assertTrue(args.dev)
        self.assertEqual(main._split_agents(args.agents), ["day-planner", "main"])

    @patch('main.ScheduleRunner')
    @patch('main.AgentsLoader')
    @patch('main.BotsLoader')
    async def test_run_bots_dev_configures_ownership(self, mock_bots_loader, mock_agents_loader, mock_schedule_runner_class):
        from core.channel.discord.ownership import DEV, Ownership
        mock_schedule_runner = MagicMock()
        mock_schedule_runner.start = AsyncMock()
        mock_schedule_runner_class.return_value = mock_schedule_runner
        mock_agents_loader.return_value.list_agent_ids.return_value = ["day-planner"]
        mock_bots_loader.return_value.get_bot.return_value = None
        mock_bots_loader.return_value._bots = {}

        await main.run_bots(dev=True, agents=["day-planner"])
        self.assertEqual(Ownership().role, DEV)
        self.assertEqual(Ownership().initial_agents, ["day-planner"])

    @patch('main.AgentsLoader')
    @patch('main.BotsLoader')
    async def test_run_bots_rejects_unknown_or_non_dev_agents(self, mock_bots_loader, mock_agents_loader):
        mock_agents_loader.return_value.list_agent_ids.return_value = ["day-planner"]
        with self.assertRaises(SystemExit):
            await main.run_bots(dev=True, agents=["nope"])
        with self.assertRaises(SystemExit):
            await main.run_bots(dev=False, agents=["day-planner"])

if __name__ == '__main__':
    unittest.main()
