import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys
import discord

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

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
        mock_bot.change_presence = AsyncMock()
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        # Verify it runs without error
        await runner.on_ready()
        
        # Verify change_presence was called
        mock_bot.change_presence.assert_called_once()

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

    @patch('core.runners.bot_runner.AgentsLoader')
    @patch('core.runners.bot_runner.commands.Bot')
    async def test_on_message_from_thread(self, mock_bot_class, mock_agents_loader_class):
        mock_bot = MagicMock()
        mock_bot.user = MagicMock()
        mock_bot.user.bot = True
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.bot = False
        mock_message.content = "Hello bot"
        mock_message.mentions = []
        mock_message.channel.send = AsyncMock()
        
        # Mock channel as a Thread
        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.name = "thread-name"
        mock_thread.parent = MagicMock()
        mock_thread.parent.name = "parent-channel-name"
        mock_message.channel = mock_thread
        
        # Mock AgentsLoader and dynamic Agent
        mock_loader = MagicMock()
        mock_agents_loader_class.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": ["parent-channel-name"]}
        mock_agent.execute = AsyncMock(return_value="reply")
        mock_loader.get_agent = MagicMock(return_value=mock_agent)
        
        await runner.on_message(mock_message)

        # Verify that it considered it a host because of the parent name
        mock_loader.get_agent.assert_called_with("main")
        mock_agent.execute.assert_called_once()

    @patch('core.runners.bot_runner.commands.Bot')
    async def test_run_bot_success(self, mock_bot_class):
        
        # Bot mock
        mock_bot = MagicMock()
        mock_bot.start = AsyncMock()
        
        # Mock is_closed to control the loop
        mock_bot.is_closed.side_effect = [False, True]
        
        # Mock async context manager
        mock_bot.__aenter__ = AsyncMock(return_value=mock_bot)
        mock_bot.__aexit__ = AsyncMock(return_value=None)
        
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
    @patch('core.runners.bot_runner.AgentsLoader')
    @patch('core.runners.bot_runner.commands.Bot')
    async def test_on_message_handles_self_vote(self, mock_bot_class, mock_agents_loader_class):
        mock_bot = MagicMock()
        mock_bot.user = MagicMock()
        mock_bot.user.bot = True
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        runner.bot.user = mock_bot.user # Ensure runner.bot.user matches
        
        mock_message = MagicMock()
        mock_message.author = mock_bot.user # Message from self
        mock_message.content = "<@123456789>: i prefer option1"
        mock_message.mentions = []
        mock_message.channel.name = "test-channel"
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
        mock_agent.config = {"channel_hosts": ["test-channel"]}
        mock_agent.execute = AsyncMock(return_value="reply")
        mock_loader.get_agent = MagicMock(return_value=mock_agent)
        
        await runner.on_message(mock_message)
        
        # Verify that execute was called with the stripped content
        mock_agent.execute.assert_called_once_with("i prefer option1", source="discord", channel=mock_message.channel, callbacks=unittest.mock.ANY)

    @patch('core.runners.bot_runner.AgentsLoader')
    @patch('core.runners.bot_runner.commands.Bot')
    async def test_on_message_ignores_self_non_vote(self, mock_bot_class, mock_agents_loader_class):
        mock_bot = MagicMock()
        mock_bot.user = MagicMock()
        mock_bot.user.bot = True
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        runner.bot.user = mock_bot.user
        
        mock_message = MagicMock()
        mock_message.author = mock_bot.user # Message from self
        mock_message.content = "Just normal text"
        mock_message.mentions = []
        mock_message.channel.send = AsyncMock()
        
        await runner.on_message(mock_message)
        
        # Should return immediately without doing anything
        mock_agents_loader_class.assert_not_called()

    @patch('core.runners.bot_runner.AgentsLoader')
    @patch('core.runners.bot_runner.commands.Bot')
    async def test_on_message_with_image_attachment(self, mock_bot_class, mock_agents_loader_class):
        mock_bot = MagicMock()
        mock_bot.user = MagicMock()
        mock_bot.user.bot = True
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.bot = False
        mock_message.content = "What is this image?"
        mock_message.mentions = [runner.bot.user]
        mock_message.channel.send = AsyncMock()
        
        # Mock attachment
        mock_attachment = MagicMock()
        mock_attachment.content_type = "image/jpeg"
        mock_attachment.read = AsyncMock(return_value=b"fake_image_data")
        mock_message.attachments = [mock_attachment]
        
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
        mock_agent.execute = AsyncMock(return_value="reply")
        mock_loader.get_agent = MagicMock(return_value=mock_agent)
        
        await runner.on_message(mock_message)
        
        # Verify that execute was called with the list payload
        mock_agent.execute.assert_called_once()
        args, kwargs = mock_agent.execute.call_args
        content_arg = args[0]
        
        self.assertIsInstance(content_arg, list)
        self.assertEqual(len(content_arg), 2)
        self.assertEqual(content_arg[0]["type"], "text")
        self.assertEqual(content_arg[1]["type"], "image_url")
        self.assertTrue(content_arg[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    @patch('core.runners.bot_runner.AgentsLoader')
    @patch('core.runners.bot_runner.commands.Bot')
    async def test_on_message_with_history_image(self, mock_bot_class, mock_agents_loader_class):
        mock_bot = MagicMock()
        mock_bot.user = MagicMock()
        mock_bot.user.bot = True
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.bot = False
        mock_message.content = "What is that image?"
        mock_message.mentions = [runner.bot.user]
        mock_message.channel.send = AsyncMock()
        mock_message.attachments = []
        
        # Mock history
        mock_history_msg = MagicMock()
        mock_attachment = MagicMock()
        mock_attachment.content_type = "image/png"
        mock_attachment.read = AsyncMock(return_value=b"fake_png_data")
        mock_history_msg.attachments = [mock_attachment]
        
        async def mock_history(limit):
            yield mock_history_msg
            
        mock_message.channel.history.return_value = mock_history(limit=10)
        
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
        mock_agent.execute = AsyncMock(return_value="reply")
        mock_loader.get_agent = MagicMock(return_value=mock_agent)
        
        await runner.on_message(mock_message)
        
        # Verify that execute was called with the list payload containing the history image
        mock_agent.execute.assert_called_once()
        args, kwargs = mock_agent.execute.call_args
        content_arg = args[0]
        
        self.assertIsInstance(content_arg, list)
        self.assertEqual(len(content_arg), 2)
        self.assertEqual(content_arg[0]["type"], "text")
        self.assertEqual(content_arg[1]["type"], "image_url")
        self.assertTrue(content_arg[1]["image_url"]["url"].startswith("data:image/png;base64,"))

if __name__ == "__main__":
    unittest.main()
