import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys
import asyncio
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
        
        # Verify event registration (on_ready, on_message, on_voice_state_update)
        self.assertEqual(mock_bot.event.call_count, 3)
        mock_bot.event.assert_any_call(runner.on_ready)
        mock_bot.event.assert_any_call(runner.on_message)
        mock_bot.event.assert_any_call(runner.on_voice_state_update)

    @patch('core.runners.bot_runner.commands.Bot')
    @patch('core.runners.bot_runner.AgentsLoader')
    async def test_on_voice_state_update_auto_follows_user(self, mock_loader_class, mock_bot_class):
        mock_bot = MagicMock()
        mock_bot_class.return_value = mock_bot
        
        mock_agent = MagicMock()
        mock_agent.config = {
            "channel_hosts": ["general", "day-planning"],
            "voice_config": {
                "enabled": True
            }
        }
        mock_loader = MagicMock()
        mock_loader.get_agent.return_value = mock_agent
        mock_loader_class.return_value = mock_loader
        
        runner = BotRunner("test_token", "main")
        runner.voice_manager = MagicMock()
        runner.voice_manager.voice_client = None
        runner.voice_manager.join_voice_channel = AsyncMock()
        runner.voice_manager.normalize_channel_name = lambda n: n.replace("-voice", "").replace("-", "")
        
        # User joins day-planning-voice
        mock_member = MagicMock(bot=False, display_name="Alva")
        mock_before = MagicMock(channel=None)
        mock_channel = MagicMock(name="day-planning-voice")
        mock_channel.name = "day-planning-voice"
        mock_after = MagicMock(channel=mock_channel)
        
        await runner.on_voice_state_update(mock_member, mock_before, mock_after)
        runner.voice_manager.join_voice_channel.assert_awaited_once_with("day-planning-voice")

    @patch('core.runners.bot_runner.commands.Bot')
    def test_get_hosted_voice_channels_convention(self, mock_bot_class):
        mock_bot = MagicMock()
        mock_bot_class.return_value = mock_bot
        runner = BotRunner("test_token", "main")
        
        mock_agent = MagicMock()
        mock_agent.config = {
            "channel_hosts": ["general", "day-planning", "agent-management"],
            "voice_config": {"enabled": True}
        }
        channels = runner.get_hosted_voice_channels(mock_agent)
        self.assertEqual(channels, ["general-voice", "day-planning-voice", "agent-management-voice"])

    @patch('core.runners.bot_runner.commands.Bot')
    @patch('core.runners.bot_runner.AgentsLoader')
    async def test_on_ready(self, mock_loader_class, mock_bot_class):
        mock_bot = MagicMock()
        mock_bot.user = "TestBot#1234"
        mock_bot.change_presence = AsyncMock()
        mock_bot.wait_until_ready = AsyncMock()
        mock_bot.guilds = []
        mock_bot_class.return_value = mock_bot
        
        mock_agent = MagicMock()
        mock_agent.config = {
            "channel_hosts": ["general"],
            "voice_config": {"enabled": True, "auto_join": True}
        }
        mock_loader = MagicMock()
        mock_loader.get_agent.return_value = mock_agent
        mock_loader_class.return_value = mock_loader

        runner = BotRunner("test_token", "main")
        runner.voice_manager.join_voice_channel = AsyncMock(return_value=True)
        
        with patch.object(asyncio, "create_task") as mock_create_task, patch("asyncio.sleep", AsyncMock()):
            await runner.on_ready()
            mock_create_task.assert_called_once()
            await mock_create_task.call_args[0][0]
        
        # Verify change_presence was called
        mock_bot.change_presence.assert_called_once()
        runner.voice_manager.join_voice_channel.assert_called_with("general-voice")

    @patch('core.runners.bot_runner.commands.Bot')
    @patch('core.runners.bot_runner.AgentsLoader')
    async def test_on_ready_picks_existing_guild_channel(self, mock_loader_class, mock_bot_class):
        mock_bot = MagicMock()
        mock_bot.user = "TestBot#1234"
        mock_bot.change_presence = AsyncMock()
        mock_bot.wait_until_ready = AsyncMock()
        
        mock_vc = MagicMock()
        mock_vc.name = "day-planning-voice"
        mock_guild = MagicMock()
        mock_guild.voice_channels = [mock_vc]
        mock_bot.guilds = [mock_guild]
        mock_bot_class.return_value = mock_bot
        
        mock_agent = MagicMock()
        mock_agent.config = {
            "channel_hosts": ["general", "day-planning"],
            "voice_config": {"enabled": True, "auto_join": True}
        }
        mock_loader = MagicMock()
        mock_loader.get_agent.return_value = mock_agent
        mock_loader_class.return_value = mock_loader

        runner = BotRunner("test_token", "main")
        runner.voice_manager.join_voice_channel = AsyncMock(return_value=True)
        
        with patch.object(asyncio, "create_task") as mock_create_task, patch("asyncio.sleep", AsyncMock()):
            await runner.on_ready()
            mock_create_task.assert_called_once()
            await mock_create_task.call_args[0][0]
        
        runner.voice_manager.join_voice_channel.assert_called_with("day-planning-voice")

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
    async def test_on_message_without_attachments_does_not_pull_history(self, mock_bot_class, mock_agents_loader_class):
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
        
        # Verify that execute was called with string payload (no history image pulled)
        mock_agent.execute.assert_called_once()
        args, kwargs = mock_agent.execute.call_args
        content_arg = args[0]
        
        self.assertEqual(content_arg, "What is that image?")

if __name__ == "__main__":
    unittest.main()
