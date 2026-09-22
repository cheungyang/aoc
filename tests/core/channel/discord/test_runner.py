import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys
import asyncio
import discord

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from core.channel.discord.runner import BotRunner

class TestBotRunner(unittest.IsolatedAsyncioTestCase):

    @patch('core.channel.discord.runner.commands.Bot')
    def test_init_registers_events(self, mock_bot_class):
        mock_bot = MagicMock()
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        # Verify event registration (on_ready, on_message, on_voice_state_update)
        self.assertEqual(mock_bot.event.call_count, 3)
        mock_bot.event.assert_any_call(runner.on_ready)
        mock_bot.event.assert_any_call(runner.on_message)
        mock_bot.event.assert_any_call(runner.on_voice_state_update)

    @patch('core.channel.discord.runner.commands.Bot')
    @patch('core.channel.discord.runner.AgentsLoader')
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

    @patch('core.channel.discord.runner.commands.Bot')
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

    @patch('core.channel.discord.runner.commands.Bot')
    @patch('core.channel.discord.runner.AgentsLoader')
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

    @patch('core.channel.discord.runner.commands.Bot')
    @patch('core.channel.discord.runner.AgentsLoader')
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

    @patch('core.channel.discord.runner.AgentsLoader')
    @patch('core.channel.discord.runner.commands.Bot')
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
        async def fake_empty_stream(*args, **kwargs):
            if False:
                yield None

        mock_loader = MagicMock()
        mock_agents_loader_class.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": ["parent-channel-name"]}
        mock_agent.execute_stream = MagicMock(side_effect=fake_empty_stream)
        mock_loader.get_agent = MagicMock(return_value=mock_agent)
        
        await runner.on_message(mock_message)

        # Verify that it considered it a host because of the parent name
        mock_loader.get_agent.assert_called_with("main")
        mock_agent.execute_stream.assert_called_once()

    @patch('core.knowledge.memory.sqlite_session_store.SqliteSessionStore.load_history', return_value=[])
    @patch('core.channel.discord.runner.AgentsLoader')
    @patch('core.channel.discord.runner.commands.Bot')
    async def test_on_message_from_thread_seeds_starter_message(self, mock_bot_class, mock_agents_loader_class, mock_load_history):
        mock_bot = MagicMock()
        mock_bot.user = MagicMock()
        mock_bot.user.bot = True
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        mock_starter = MagicMock()
        mock_starter.id = 1111111111111111111
        mock_starter.author = MagicMock(display_name="Alva")
        mock_starter.content = "Initial topic outline"

        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.id = 1541110915540324533
        mock_thread.name = "AI thread"
        mock_thread.parent = MagicMock()
        mock_thread.parent.name = "topic-research"
        mock_thread.starter_message = mock_starter

        mock_message = MagicMock()
        mock_message.id = 2222222222222222222
        mock_message.author = MagicMock(bot=False)
        mock_message.content = "Let's begin chapter 1"
        mock_message.mentions = []
        mock_message.channel = mock_thread
        mock_message.attachments = []
        
        captured_payload = None
        async def fake_stream(payload, *args, **kwargs):
            nonlocal captured_payload
            captured_payload = payload
            if False:
                yield None

        mock_loader = MagicMock()
        mock_agents_loader_class.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": ["topic-research"]}
        mock_agent.execute_stream = MagicMock(side_effect=fake_stream)
        mock_loader.get_agent = MagicMock(return_value=mock_agent)
        
        await runner.on_message(mock_message)

        mock_agent.execute_stream.assert_called_once()
        self.assertIn('[Thread starter message from Alva: "Initial topic outline"]', captured_payload)
        self.assertIn("Let's begin chapter 1", captured_payload)

    @patch('core.channel.discord.runner.commands.Bot')
    async def test_run_bot_success(self, mock_bot_class):
        # Bot mock
        mock_bot = MagicMock()
        mock_bot.is_closed.return_value = False
        
        # Mock async context manager
        mock_bot.__aenter__ = AsyncMock(return_value=mock_bot)
        mock_bot.__aexit__ = AsyncMock(return_value=None)
        
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        async def fake_start(token):
            await runner.stop()
        mock_bot.start = AsyncMock(side_effect=fake_start)
        
        # Run
        await runner.run_bot()
        
        # Assertions
        mock_bot.start.assert_called_once_with("test_token")

    @patch('core.channel.discord.runner.commands.Bot')
    @patch('asyncio.sleep', AsyncMock())
    async def test_run_bot_reconnects_on_disconnect(self, mock_bot_class):
        mock_bot1 = MagicMock()
        mock_bot1.is_closed.return_value = False
        mock_bot1.__aenter__ = AsyncMock(return_value=mock_bot1)
        mock_bot1.__aexit__ = AsyncMock(return_value=None)
        
        mock_bot2 = MagicMock()
        mock_bot2.is_closed.return_value = False
        mock_bot2.__aenter__ = AsyncMock(return_value=mock_bot2)
        mock_bot2.__aexit__ = AsyncMock(return_value=None)
        
        mock_bot_class.side_effect = [mock_bot1, mock_bot2]
        
        runner = BotRunner("test_token", "main")
        
        # First start raises a network error; second start stops runner
        async def fake_start_1(token):
            mock_bot1.is_closed.return_value = True
            raise ConnectionResetError("Connection lost")
            
        async def fake_start_2(token):
            await runner.stop()
            
        mock_bot1.start = AsyncMock(side_effect=fake_start_1)
        mock_bot2.start = AsyncMock(side_effect=fake_start_2)
        
        await runner.run_bot()
        
        mock_bot1.start.assert_called_once_with("test_token")
        mock_bot2.start.assert_called_once_with("test_token")

    @patch('core.channel.discord.runner.AgentsLoader')
    @patch('core.channel.discord.runner.commands.Bot')
    async def test_on_message_long_reply(self, mock_bot_class, mock_agents_loader_class):
        """A reply over Discord's 2,000-char message limit must reach the channel intact,
        split across several sends.

        This test used to stub `agent.execute`, but on_message streams through
        `agent.execute_stream` (bot_runner.py:258). The 4,500-char response was therefore
        never produced, and the only assertion (`send.call_count == 0`) would have held
        even if on_message had been deleted outright. It now drives the real stream and
        the real DiscordStreamBuffer chunking.
        """
        mock_bot = MagicMock()
        mock_bot.user = MagicMock()
        mock_bot.user.bot = True
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        mock_message = MagicMock()
        mock_message.author = MagicMock(bot=False)
        mock_message.content = "Hello bot"
        mock_message.mentions = [runner.bot.user]
        mock_message.attachments = []
        
        # Record every chunk posted to the channel; hand back an editable message stub so
        # the buffer's later edit/delete passes behave like real discord.Message objects.
        sent_chunks = []

        async def fake_send(content=None, **kwargs):
            sent_chunks.append(content)
            sent_msg = MagicMock()
            sent_msg.edit = AsyncMock()
            sent_msg.delete = AsyncMock()
            return sent_msg

        mock_message.channel.send = AsyncMock(side_effect=fake_send)
        
        # Mock channel.typing context manager
        mock_typing = MagicMock()
        mock_typing.__aenter__ = AsyncMock()
        mock_typing.__aexit__ = AsyncMock()
        mock_message.channel.typing.return_value = mock_typing
        
        # ~4,600 chars of distinguishable text. No trailing whitespace: the buffer
        # rstrips the filtered text, which would break exact reassembly below.
        long_response = "\n".join(f"Line {i:03d}: " + ("detail " * 8).strip() for i in range(70))
        self.assertGreater(len(long_response), 4000)

        async def fake_stream(*args, **kwargs):
            yield {"type": "token", "content": long_response}
            yield {"type": "final_response", "text": long_response, "response": None}

        # Mock AgentsLoader and dynamic Agent
        mock_loader = MagicMock()
        mock_agents_loader_class.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": []}
        mock_agent.execute_stream = fake_stream
        mock_loader.get_agent = MagicMock(return_value=mock_agent)
        
        await runner.on_message(mock_message)
 
        # The reply must actually be split: more than one send...
        self.assertGreater(len(sent_chunks), 1)
        # ...every chunk within Discord's hard 2,000-char limit...
        for chunk in sent_chunks:
            self.assertLessEqual(len(chunk), 2000)
        # ...and the chunks must reassemble to exactly the response (no loss, no
        # reordering, and no error message tacked on by the exception handler).
        self.assertEqual("".join(sent_chunks), long_response)

    @patch('core.channel.discord.runner.AgentsLoader')
    @patch('core.channel.discord.runner.commands.Bot')
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
        async def fake_empty_stream(*args, **kwargs):
            if False:
                yield None

        mock_loader = MagicMock()
        mock_agents_loader_class.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": ["test-channel"]}
        mock_agent.execute_stream = MagicMock(side_effect=fake_empty_stream)
        mock_loader.get_agent = MagicMock(return_value=mock_agent)
        
        await runner.on_message(mock_message)
        
        # Verify that execute_stream was called with the stripped content
        mock_agent.execute_stream.assert_called_once_with("i prefer option1", session=unittest.mock.ANY, callbacks=unittest.mock.ANY)
        called_session = mock_agent.execute_stream.call_args[1]["session"]
        self.assertEqual(called_session.agent_id, "main")
        self.assertEqual(called_session.source, "discord")

    @patch('core.channel.discord.runner.AgentsLoader')
    @patch('core.channel.discord.runner.commands.Bot')
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

    @patch('core.channel.discord.runner.AgentsLoader')
    @patch('core.channel.discord.runner.commands.Bot')
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
        async def fake_empty_stream(*args, **kwargs):
            if False:
                yield None

        mock_loader = MagicMock()
        mock_agents_loader_class.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": []}
        mock_agent.execute_stream = MagicMock(side_effect=fake_empty_stream)
        mock_loader.get_agent = MagicMock(return_value=mock_agent)
        
        await runner.on_message(mock_message)
        
        # Verify that execute_stream was called with the list payload
        mock_agent.execute_stream.assert_called_once()
        args, kwargs = mock_agent.execute_stream.call_args
        content_arg = args[0]
        
        self.assertIsInstance(content_arg, list)
        self.assertEqual(len(content_arg), 2)
        self.assertEqual(content_arg[0]["type"], "text")
        self.assertEqual(content_arg[1]["type"], "image_url")
        self.assertTrue(content_arg[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    @patch('core.knowledge.memory.sqlite_session_store.SqliteSessionStore.load_history')
    @patch('core.channel.discord.runner.AgentsLoader')
    @patch('core.channel.discord.runner.commands.Bot')
    async def test_on_message_does_not_reseed_thread_starter_when_history_exists(self, mock_bot_class, mock_agents_loader_class, mock_load_history):
        """History must not be replayed into the prompt on every turn.

        The original version of this test was named
        `test_on_message_without_attachments_does_not_pull_history`, from a time when
        on_message scraped Discord history looking for a previous image. That code is
        gone; the only history access left is the SqliteSessionStore.load_history guard
        at bot_runner.py:242, which exists so a thread's starter message is injected
        *once* (on the first turn) and never again. This test pins that guard: with a
        non-empty stored history, the starter text must be absent and the payload must
        stay the user's plain message string.
        """
        mock_bot = MagicMock()
        mock_bot.user = MagicMock()
        mock_bot.user.bot = True
        mock_bot_class.return_value = mock_bot
        
        runner = BotRunner("test_token", "main")
        
        mock_starter = MagicMock()
        mock_starter.id = 1111111111111111111
        mock_starter.author = MagicMock(display_name="Alva")
        mock_starter.content = "Initial topic outline"

        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.id = 1541110915540324533
        mock_thread.name = "AI thread"
        mock_thread.parent = MagicMock()
        mock_thread.parent.name = "topic-research"
        mock_thread.starter_message = mock_starter

        mock_message = MagicMock()
        mock_message.id = 3333333333333333333
        mock_message.author = MagicMock(bot=False)
        mock_message.content = "What is that image?"
        mock_message.mentions = []
        mock_message.channel = mock_thread
        mock_message.attachments = []
        
        # The session already has stored turns -> starter seeding must be skipped
        mock_load_history.return_value = [{"role": "user", "content": "earlier turn"}]
        
        captured_payload = None
        async def fake_stream(payload, *args, **kwargs):
            nonlocal captured_payload
            captured_payload = payload
            if False:
                yield None

        mock_loader = MagicMock()
        mock_agents_loader_class.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": ["topic-research"]}
        mock_agent.execute_stream = MagicMock(side_effect=fake_stream)
        mock_loader.get_agent = MagicMock(return_value=mock_agent)
        
        await runner.on_message(mock_message)
        
        mock_agent.execute_stream.assert_called_once()
        # The history guard must actually have been consulted...
        mock_load_history.assert_called_once()
        # ...and its non-empty result must suppress the starter-message prefix.
        self.assertEqual(captured_payload, "What is that image?")
        self.assertNotIn("[Thread starter message", captured_payload)

    @patch('core.channel.discord.stream_buffer.DiscordStreamBuffer.finalize')
    @patch('core.channel.discord.stream_buffer.DiscordStreamBuffer.append_token')
    @patch('core.channel.discord.runner.AgentsLoader')
    @patch('core.channel.discord.runner.commands.Bot')
    async def test_on_message_streams_with_discord_stream_buffer(self, mock_bot_class, mock_agents_loader_class, mock_append_token, mock_finalize):
        mock_bot = MagicMock()
        mock_bot.user = MagicMock()
        mock_bot.user.bot = True
        mock_bot_class.return_value = mock_bot

        runner = BotRunner("test_token", "main")

        mock_message = MagicMock()
        mock_message.author = MagicMock(bot=False)
        mock_message.content = "Stream this test"
        mock_message.mentions = [runner.bot.user]
        mock_message.channel.send = AsyncMock()
        mock_message.attachments = []

        mock_typing = MagicMock()
        mock_typing.__aenter__ = AsyncMock()
        mock_typing.__aexit__ = AsyncMock()
        mock_message.channel.typing.return_value = mock_typing

        async def fake_stream(*args, **kwargs):
            yield {"type": "token", "content": "Hello "}
            yield {"type": "token", "content": "world!"}
            yield {"type": "final_response", "text": "Hello world!", "response": None}

        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": []}
        mock_agent.execute_stream = fake_stream

        mock_loader = MagicMock()
        mock_loader.get_agent = MagicMock(return_value=mock_agent)
        mock_agents_loader_class.return_value = mock_loader

        await runner.on_message(mock_message)

        self.assertEqual(mock_append_token.call_count, 2)
        mock_finalize.assert_called_once_with(final_text="Hello world!", response=None)

    @patch('core.channel.discord.runner.AgentsLoader')
    @patch('core.channel.discord.runner.commands.Bot')
    async def test_on_message_deduplicates_identical_message_ids(self, mock_bot_class, mock_agents_loader_class):
        mock_bot = MagicMock()
        mock_bot.user = MagicMock()
        mock_bot.user.bot = True
        mock_bot_class.return_value = mock_bot

        runner = BotRunner("test_token", "main")

        mock_message = MagicMock()
        mock_message.id = 9988776655
        mock_message.author = MagicMock(bot=False)
        mock_message.content = "Test deduplication"
        mock_message.mentions = [runner.bot.user]
        mock_message.attachments = []

        mock_typing = MagicMock()
        mock_typing.__aenter__ = AsyncMock()
        mock_typing.__aexit__ = AsyncMock()
        mock_message.channel.typing.return_value = mock_typing

        called_count = 0
        async def fake_stream(*args, **kwargs):
            nonlocal called_count
            called_count += 1
            if False:
                yield None

        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": []}
        mock_agent.execute_stream = fake_stream

        mock_loader = MagicMock()
        mock_loader.get_agent = MagicMock(return_value=mock_agent)
        mock_agents_loader_class.return_value = mock_loader

        # First delivery -> processed
        await runner.on_message(mock_message)
        self.assertEqual(called_count, 1)

        # Second delivery with same message ID -> dropped
        await runner.on_message(mock_message)
        self.assertEqual(called_count, 1)

if __name__ == "__main__":
    unittest.main()
