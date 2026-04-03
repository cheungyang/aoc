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
        
        runner = BotRunner("test_token")
        
        # Verify event registration
        self.assertEqual(mock_bot.event.call_count, 2)
        mock_bot.event.assert_any_call(runner.on_ready)
        mock_bot.event.assert_any_call(runner.on_message)

    @patch('core.bot_runner.commands.Bot')
    async def test_on_ready(self, mock_bot_class):
        mock_bot = MagicMock()
        mock_bot.user = "TestBot#1234"
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token")
        
        # Verify it runs without error
        await runner.on_ready()

    @patch('core.bot_runner.commands.Bot')
    @patch('core.agent_builder.AgentBuilder')
    async def test_on_message_ignores_self(self, mock_agent_builder_class, mock_bot_class):
        mock_bot = MagicMock()
        mock_bot.user = "TestBot#1234"
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token")
        
        mock_message = MagicMock()
        mock_message.author = "TestBot#1234" # Self
        
        await runner.on_message(mock_message)
        
        # Should return immediately without doing anything
        mock_agent_builder_class.assert_not_called()

    @patch('core.agent_builder.AgentBuilder')
    @patch('core.bot_runner.commands.Bot')
    async def test_on_message_delegates(self, mock_bot_class, mock_agent_builder_class):
        mock_bot = MagicMock()
        mock_bot.user = "TestBot#1234"
        mock_bot.mcp_tools = ["tool1", "tool2"]
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token")
        
        mock_message = MagicMock()
        mock_message.author = "User#9999"
        mock_message.content = "Hello bot"
        
        # Mock AgentBuilder and dynamic Agent
        mock_builder = MagicMock()
        mock_agent_builder_class.return_value = mock_builder
        mock_agent = MagicMock()
        mock_agent.process_message = AsyncMock()
        mock_builder.build_agent.return_value = mock_agent
        
        await runner.on_message(mock_message)

        mock_agent_builder_class.assert_called_once_with(["tool1", "tool2"])
        mock_agent.process_message.assert_called_once_with(mock_message, runner.bot)

    @patch('core.bot_runner.load_mcp_tools')
    @patch('core.mcp_manager.MCPClientManager.get_session')
    @patch('core.bot_runner.commands.Bot')
    async def test_run_bot_success(self, mock_bot_class, mock_get_session, mock_load_tools):
        # Setup mocks
        mock_session = MagicMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session
        mock_load_tools.return_value = ["tool1", "tool2"]
        
        # Bot mock
        mock_bot = AsyncMock()
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token")

        # Run
        await runner.run_bot()

        # Assertions
        mock_get_session.assert_called_once()
        mock_load_tools.assert_called_once_with(mock_session)
        self.assertEqual(runner.bot.mcp_tools, ["tool1", "tool2"])
        mock_bot.start.assert_called_once_with("test_token")

if __name__ == "__main__":
    unittest.main()
