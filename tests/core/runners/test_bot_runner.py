import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.runners.bot_runner import BotRunner

class TestBotRunner(unittest.IsolatedAsyncioTestCase):

    @patch('core.runners.bot_runner.commands.Bot')
    def test_init_registers_events(self, mock_bot_class):
        mock_bot = MagicMock()
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        # Verify event registration
        self.assertEqual(mock_bot.event.call_count, 2)
        mock_bot.event.assert_any_call(runner.on_ready)
        mock_bot.event.assert_any_call(runner.on_message)

    @patch('core.runners.bot_runner.commands.Bot')
    async def test_on_ready(self, mock_bot_class):
        mock_bot = MagicMock()
        mock_bot.user = "TestBot#1234"
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        # Verify it runs without error
        await runner.on_ready()

    @patch('core.runners.bot_runner.commands.Bot')
    @patch('core.runners.bot_runner.AgentsLoader')
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

    @patch('core.runners.bot_runner.AgentsLoader')
    @patch('core.runners.bot_runner.commands.Bot')
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
        mock_message.channel.send = AsyncMock()
        
        # Mock AgentsLoader and dynamic Agent
        mock_loader = MagicMock()
        mock_agents_loader_class.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": []}
        mock_agent.execute = AsyncMock(return_value="reply")
        mock_loader.get_agent = MagicMock(return_value=mock_agent)
        
        await runner.on_message(mock_message)

        mock_loader.get_agent.assert_called_with("main")
        mock_agent.execute.assert_called_once()

    @patch('core.runners.bot_runner.commands.Bot')
    async def test_run_bot_success(self, mock_bot_class):
        
        # Bot mock
        mock_bot = AsyncMock()
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        # Run
        await runner.run_bot()
        
        # Assertions

        mock_bot.start.assert_called_once_with("test_token")

    @patch('core.runners.bot_runner.AgentsLoader')
    @patch('core.runners.bot_runner.commands.Bot')
    async def test_on_message_long_reply(self, mock_bot_class, mock_agents_loader_class):
        mock_bot = MagicMock()
        mock_bot.user = MagicMock()
        mock_bot.user.bot = True
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.bot = False
        mock_message.content = "Hello bot"
        mock_message.mentions = [runner.bot.user]
        mock_message.channel.send = AsyncMock()
        
        # Mock channel.typing context manager
        mock_typing = MagicMock()
        mock_typing.__aenter__ = AsyncMock()
        mock_typing.__aexit__ = AsyncMock()
        mock_message.channel.typing.return_value = mock_typing
        
        # Mock AgentsLoader and dynamic Agent
        mock_loader = MagicMock()
        mock_agents_loader_class.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": []}
        long_response = "a" * 4500
        mock_agent.execute = AsyncMock(return_value=long_response)
        mock_loader.get_agent = MagicMock(return_value=mock_agent)
        
        await runner.on_message(mock_message)
 
        self.assertEqual(mock_message.channel.send.call_count, 0)

if __name__ == "__main__":
    unittest.main()
