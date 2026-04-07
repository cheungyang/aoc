import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.bot_runner import BotRunner

class TestBotRunner(unittest.IsolatedAsyncioTestCase):

    @patch('core.bot_runner.commands.Bot')
    def test_init_registers_events(self, mock_bot_class):
        mock_bot = MagicMock()
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        # Verify event registration
        self.assertEqual(mock_bot.event.call_count, 2)
        mock_bot.event.assert_any_call(runner.on_ready)
        mock_bot.event.assert_any_call(runner.on_message)

    @patch('core.bot_runner.commands.Bot')
    async def test_on_ready(self, mock_bot_class):
        mock_bot = MagicMock()
        mock_bot.user = "TestBot#1234"
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        # Verify it runs without error
        await runner.on_ready()

    @patch('core.bot_runner.commands.Bot')
    @patch('core.agent.agents_loader.AgentsLoader')
    async def test_on_message_ignores_self(self, mock_agents_loader_class, mock_bot_class):
        mock_bot = MagicMock()
        mock_bot.user = "TestBot#1234"
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.bot = True
        
        await runner.on_message(mock_message)
        
        # Should return immediately without doing anything
        mock_agents_loader_class.assert_not_called()

    @patch('core.agent.agents_loader.AgentsLoader')
    @patch('core.bot_runner.commands.Bot')
    async def test_on_message_delegates(self, mock_bot_class, mock_agents_loader_class):
        mock_bot = MagicMock()
        mock_bot.user = MagicMock()
        mock_bot.user.bot = True
        mock_bot.mcp_tools = ["tool1", "tool2"]
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.bot = False
        mock_message.content = "Hello bot"
        mock_message.mentions = [runner.bot.user]
        
        # Mock AgentsLoader and dynamic Agent
        mock_loader = MagicMock()
        mock_agents_loader_class.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.process_message = AsyncMock()
        mock_loader.get_agent = AsyncMock(return_value=mock_agent)
        
        await runner.on_message(mock_message)

        mock_loader.get_agent.assert_called_once_with("main", runner.bot.mcp_session)
        mock_agent.process_message.assert_called_once_with(mock_message, runner.bot)

    @patch('core.mcp_manager.MCPClientManager.get_session')
    @patch('core.bot_runner.commands.Bot')
    async def test_run_bot_success(self, mock_bot_class, mock_get_session):
        # Setup mocks
        mock_session = MagicMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session
        
        # Bot mock
        mock_bot = AsyncMock()
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        # Run
        await runner.run_bot()
        
        # Assertions
        mock_get_session.assert_called_once()
        mock_bot.start.assert_called_once_with("test_token")

if __name__ == "__main__":
    unittest.main()
