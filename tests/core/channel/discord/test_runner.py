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
        
        # Verify event registration (on_ready, on_message, on_voice_state_update,
        # on_raw_reaction_add, on_raw_poll_vote_add)
        self.assertEqual(mock_bot.event.call_count, 5)
        mock_bot.event.assert_any_call(runner.on_ready)
        mock_bot.event.assert_any_call(runner.on_message)
        mock_bot.event.assert_any_call(runner.on_voice_state_update)
        mock_bot.event.assert_any_call(runner.on_raw_poll_vote_add)
        # Commands, `!help` among them, are core/runtime/commands/, run by
        # on_message: discord.py's own command handling and help stay unused.
        self.assertIsNone(mock_bot_class.call_args.kwargs["help_command"])
        mock_bot.add_command.assert_not_called()

    def _voice_runner(self):
        """A runner whose voice room is a stand-in: what the runner forwards to it."""
        runner = BotRunner("test_token", "main")
        runner.voice_manager = MagicMock()
        runner.voice_manager.member_moved = AsyncMock()
        return runner

    @patch('core.channel.discord.runner.commands.Bot')
    async def test_on_voice_state_update_forwards_your_moves_to_the_voice_room(self, mock_bot_class):
        """Following you into the voice channel is the voice room's
        (VoiceManager.member_moved); the runner only forwards."""
        mock_bot_class.return_value = MagicMock()
        runner = self._voice_runner()

        member, before, after = MagicMock(bot=False), MagicMock(), MagicMock()
        await runner.on_voice_state_update(member, before, after)

        runner.voice_manager.member_moved.assert_awaited_once_with(member, before, after)

    @patch('core.channel.discord.runner.commands.Bot')
    async def test_on_voice_state_update_ignores_bots_and_agents_owned_elsewhere(self, mock_bot_class):
        mock_bot_class.return_value = MagicMock()
        runner = self._voice_runner()

        await runner.on_voice_state_update(MagicMock(bot=True), MagicMock(), MagicMock())
        with patch('core.channel.discord.runner.Ownership') as ownership:
            ownership.return_value.is_mine.return_value = False
            await runner.on_voice_state_update(MagicMock(bot=False), MagicMock(), MagicMock())

        runner.voice_manager.member_moved.assert_not_awaited()

    @patch('core.channel.discord.runner.commands.Bot')
    async def test_on_ready_auto_joins_through_the_voice_room(self, mock_bot_class):
        """Whether and where to auto-join is the voice room's (VoiceManager.auto_join)."""
        mock_bot = MagicMock()
        mock_bot.user = "TestBot#1234"
        mock_bot.change_presence = AsyncMock()
        mock_bot.wait_until_ready = AsyncMock()
        mock_bot.guilds = []
        mock_bot_class.return_value = mock_bot

        runner = BotRunner("test_token", "main")
        runner.voice_manager = MagicMock()
        await runner.on_ready()

        mock_bot.change_presence.assert_called_once()
        runner.voice_manager.auto_join.assert_called_once_with()

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

    @patch('core.storage.sessions.SessionStore.load_history', return_value=[])
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
        mock_starter.author = MagicMock(display_name="Sam")
        mock_starter.content = "Initial topic outline"

        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.id = 123456789012345678
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
        self.assertIn('[Thread starter message from Sam: "Initial topic outline"]', captured_payload)
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
        # Messages break at line boundaries, so each break stands for a newline.
        self.assertEqual("\n".join(sent_chunks), long_response)

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
    async def test_on_message_remembers_your_message_for_the_now_reaction(self, mock_bot_class, mock_agents_loader_class):
        """The turn's output names the message it answers: a live `now` route puts its
        recipient's emoji there (core/proactive/pipeline.py)."""
        mock_bot = MagicMock()
        mock_bot.user = MagicMock()
        mock_bot.user.bot = True
        mock_bot_class.return_value = mock_bot
        runner = BotRunner("test_token", "main")

        mock_message = MagicMock()
        mock_message.id = 222
        mock_message.author = MagicMock()
        mock_message.author.bot = False
        mock_message.content = "here's my receipt"
        mock_message.mentions = []
        mock_message.channel.id = 111
        mock_message.channel.name = "receipts"
        mock_message.channel.send = AsyncMock()
        mock_typing = MagicMock()
        mock_typing.__aenter__ = AsyncMock()
        mock_typing.__aexit__ = AsyncMock()
        mock_message.channel.typing.return_value = mock_typing

        async def fake_empty_stream(*args, **kwargs):
            if False:
                yield None

        mock_loader = MagicMock()
        mock_agents_loader_class.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": ["receipts"]}
        mock_agent.execute_stream = MagicMock(side_effect=fake_empty_stream)
        mock_loader.get_agent = MagicMock(return_value=mock_agent)

        await runner.on_message(mock_message)

        session = mock_agent.execute_stream.call_args.kwargs["session"]
        self.assertEqual(session.output.source_message, "111/222")

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

    @patch('core.storage.sessions.SessionStore.load_history')
    @patch('core.channel.discord.runner.AgentsLoader')
    @patch('core.channel.discord.runner.commands.Bot')
    async def test_on_message_does_not_reseed_thread_starter_when_history_exists(self, mock_bot_class, mock_agents_loader_class, mock_load_history):
        """History must not be replayed into the prompt on every turn.

        The original version of this test was named
        `test_on_message_without_attachments_does_not_pull_history`, from a time when
        on_message scraped Discord history looking for a previous image. That code is
        gone; the only history access left is the SessionStore.load_history guard
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
        mock_starter.author = MagicMock(display_name="Sam")
        mock_starter.content = "Initial topic outline"

        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.id = 123456789012345678
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


@patch('core.channel.discord.runner.AgentsLoader')
@patch('core.channel.discord.runner.commands.Bot')
class TestTypedCommands(unittest.IsolatedAsyncioTestCase):
    """`!name` messages: the runner runs them from core/runtime/commands/
    itself, and they never reach the agent."""

    def _runner(self, mock_bot_class, mock_loader_class, hosts=("general",)):
        mock_bot = MagicMock()
        mock_bot.user = MagicMock(bot=True)
        mock_bot_class.return_value = mock_bot
        runner = BotRunner("test_token", "main")
        agent = MagicMock()
        agent.config = {"channel_hosts": list(hosts)}
        mock_loader_class.return_value.get_agent.return_value = agent
        return runner, agent

    @staticmethod
    def _message(content, mentions=()):
        message = MagicMock()
        message.author = MagicMock(bot=False)
        message.content = content
        message.mentions = list(mentions)
        message.attachments = []
        message.channel.name = "general"
        message.channel.send = AsyncMock()
        return message

    async def test_help_is_one_of_ours(self, mock_bot_class, mock_loader_class):
        """discord.py's help is off; `!help` lists every command there is."""
        runner, agent = self._runner(mock_bot_class, mock_loader_class)
        message = self._message("!help")
        await runner.on_message(message)

        text = "\n".join(c.args[0] for c in message.channel.send.await_args_list)
        self.assertIn("`!new`", text)
        self.assertIn("**Said in the voice room**", text)
        agent.execute_stream.assert_not_called()

    async def test_a_command_runs_on_the_channels_session(self, mock_bot_class, mock_loader_class):
        runner, agent = self._runner(mock_bot_class, mock_loader_class)
        message = self._message("!new")
        with patch('core.channel.discord.runner.TYPED') as typed:
            typed.answer = AsyncMock()
            await runner.on_message(message)

        text, ctx = typed.answer.await_args.args
        self.assertEqual(text, "!new")
        self.assertEqual((ctx.channel, ctx.agent_id, ctx.answers_here), (message.channel, "main", True))
        self.assertEqual((ctx.session.agent_id, ctx.session.source, ctx.session.channel_name),
                         ("main", "discord", "general"))
        self.assertIs(ctx.voice, runner.voice_manager)
        agent.execute_stream.assert_not_called()

    async def test_it_answers_in_a_channel_it_hosts_or_where_it_is_tagged(self, mock_bot_class, mock_loader_class):
        """As a message there would be answered. Voice room commands don't ask
        (VoiceRoomCommand), so the registry hears every `!` message."""
        runner, _agent = self._runner(mock_bot_class, mock_loader_class, hosts=())
        other_bot = MagicMock(bot=True)
        with patch('core.channel.discord.runner.TYPED') as typed:
            typed.answer = AsyncMock()
            await runner.on_message(self._message("!new"))
            await runner.on_message(self._message("!new", mentions=[runner.bot.user]))
            await runner.on_message(self._message("!new", mentions=[other_bot]))
        self.assertEqual([c.args[1].answers_here for c in typed.answer.await_args_list], [False, True, False])

        runner, _agent = self._runner(mock_bot_class, mock_loader_class)  # hosts #general
        with patch('core.channel.discord.runner.TYPED') as typed:
            typed.answer = AsyncMock()
            await runner.on_message(self._message("!new"))
            await runner.on_message(self._message("!new", mentions=[other_bot]))
        self.assertEqual([c.args[1].answers_here for c in typed.answer.await_args_list], [True, False])

    async def test_never_for_an_agent_held_elsewhere_or_from_a_bot(self, mock_bot_class, mock_loader_class):
        runner, _agent = self._runner(mock_bot_class, mock_loader_class)
        own_vote = self._message("<@1>: !restart")
        own_vote.author = runner.bot.user  # the bot's own poll vote, mention stripped
        with patch('core.channel.discord.runner.TYPED') as typed, \
                patch('core.channel.discord.runner.Ownership') as ownership:
            typed.answer = AsyncMock()
            ownership.return_value.is_control_channel.return_value = False
            ownership.return_value.is_mine.return_value = False
            await runner.on_message(self._message("!restart"))
            ownership.return_value.is_mine.return_value = True
            await runner.on_message(own_vote)
        typed.answer.assert_not_awaited()

    async def test_a_failing_command_is_logged_and_the_bot_carries_on(self, mock_bot_class, mock_loader_class):
        runner, agent = self._runner(mock_bot_class, mock_loader_class)
        with patch('core.channel.discord.runner.TYPED') as typed:
            typed.answer = AsyncMock(side_effect=RuntimeError("boom"))
            await runner.on_message(self._message("!compact"))
        agent.execute_stream.assert_not_called()


@patch('core.channel.discord.runner.record_reply')
@patch('core.channel.discord.runner.mark_read')
@patch('core.channel.discord.runner.AgentsLoader')
@patch('core.channel.discord.runner.commands.Bot')
class TestInboxSignals(unittest.IsolatedAsyncioTestCase):
    """The runner's part of the channel inbox (core/voice/briefing): posting in a
    channel or ✅ on a post finishes its row; a reply is a candidate."""

    def _runner(self, mock_bot_class, mock_loader_class, voice=True, hosts=("day-planning",), reply="Good, noted."):
        import datetime
        mock_bot = MagicMock()
        mock_bot.user = MagicMock()
        mock_bot.user.bot = True
        mock_bot_class.return_value = mock_bot
        runner = BotRunner("test_token", "main")
        # Whether this bot hosts the voice room (VoiceManager.hosts_voice).
        runner.voice_manager.hosts_voice = MagicMock(return_value=voice)

        async def fake_stream(*args, **kwargs):
            yield {"type": "final_response", "text": reply, "response": None}

        agent = MagicMock()
        agent.config = {"channel_hosts": list(hosts), "voice_config": {"enabled": voice}}
        agent.execute_stream = fake_stream
        mock_loader_class.return_value.get_agent.return_value = agent

        message = MagicMock()
        message.id = 4242
        message.author = MagicMock(bot=False)
        message.content = "About a seven."
        message.mentions = []
        message.attachments = []
        message.channel.name = "day-planning"
        message.channel.id = 77
        message.created_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=12)
        typing = MagicMock()
        typing.__aenter__ = AsyncMock()
        typing.__aexit__ = AsyncMock()
        message.channel.typing.return_value = typing
        return runner, message

    async def test_posting_finishes_the_row_and_the_reply_is_a_candidate(self, mock_bot_class, mock_loader_class,
                                                                          mark_read, record_reply):
        from core.storage.channel_inbox import Via
        runner, message = self._runner(mock_bot_class, mock_loader_class)
        with patch('core.channel.discord.stream_buffer.DiscordStreamBuffer.finalize', new=AsyncMock()):
            await runner.on_message(message)

        mark_read.assert_called_once_with(message.channel, Via.TYPED)
        record_reply.assert_called_once()
        args, kwargs = record_reply.call_args
        self.assertEqual(args, (message.channel, "Good, noted."))
        self.assertEqual(kwargs["agent_id"], "main")
        self.assertAlmostEqual(kwargs["asked_at"], message.created_at.timestamp())

    async def test_a_channel_it_doesnt_host_leaves_the_inbox_alone(self, mock_bot_class, mock_loader_class,
                                                                    mark_read, record_reply):
        runner, message = self._runner(mock_bot_class, mock_loader_class, hosts=())
        message.mentions = [runner.bot.user]  # tagged, so it answers
        with patch('core.channel.discord.stream_buffer.DiscordStreamBuffer.finalize', new=AsyncMock()):
            await runner.on_message(message)
        mark_read.assert_not_called()
        record_reply.assert_not_called()

    async def test_a_failed_reply_is_no_candidate(self, mock_bot_class, mock_loader_class, mark_read, record_reply):
        runner, message = self._runner(mock_bot_class, mock_loader_class)

        async def broken_stream(*args, **kwargs):
            raise RuntimeError("model down")
            yield  # pragma: no cover

        mock_loader_class.return_value.get_agent.return_value.execute_stream = broken_stream
        message.channel.send = AsyncMock()
        await runner.on_message(message)
        mark_read.assert_called_once()
        record_reply.assert_not_called()

    async def test_a_check_mark_finishes_the_posts_row(self, mock_bot_class, mock_loader_class, mark_read, record_reply):
        import datetime
        import discord
        from core.storage.channel_inbox import Via
        runner, message = self._runner(mock_bot_class, mock_loader_class)
        runner.bot.get_channel.return_value = message.channel
        posted = datetime.datetime(2026, 10, 22, 6, 0, tzinfo=datetime.timezone.utc)
        payload = MagicMock(emoji="✅", member=MagicMock(bot=False), channel_id=77,
                            message_id=discord.utils.time_snowflake(posted))

        await runner.on_raw_reaction_add(payload)
        mark_read.assert_called_once()
        args, kwargs = mark_read.call_args
        self.assertEqual(args, (message.channel, Via.REACTION))
        self.assertAlmostEqual(kwargs["message_time"], posted.timestamp(), places=0)

    async def test_other_reactions_bots_and_other_bots_runners_do_nothing(self, mock_bot_class, mock_loader_class,
                                                                          mark_read, record_reply):
        runner, message = self._runner(mock_bot_class, mock_loader_class)
        runner.bot.get_channel.return_value = message.channel
        for payload in (MagicMock(emoji="👍", member=MagicMock(bot=False), message_id=1),
                        MagicMock(emoji="✅", member=MagicMock(bot=True), message_id=1),
                        MagicMock(emoji="✅", member=None, message_id=1)):
            await runner.on_raw_reaction_add(payload)
        not_voice, _ = self._runner(mock_bot_class, mock_loader_class, voice=False)
        await not_voice.on_raw_reaction_add(MagicMock(emoji="✅", member=MagicMock(bot=False), message_id=1))
        mark_read.assert_not_called()

    async def test_a_mark_on_a_gate_review_post_labels_the_utterance_not_the_inbox(self, mock_bot_class,
                                                                                  mock_loader_class, mark_read,
                                                                                  record_reply):
        from core.storage.speaker_gate_log import GateRecord, Label, Mode
        runner, message = self._runner(mock_bot_class, mock_loader_class)
        runner.bot.get_channel.return_value = message.channel
        log = runner.voice_manager.speaker_gate.log
        record_id = log.add(GateRecord(at=1.0, mode=Mode.SHADOW, model="m.onnx", duration_ms=1500,
                                       verdict="NOT_YOU", score=0.3))
        log.set_review_message(record_id, 31337)

        await runner.on_raw_reaction_add(MagicMock(emoji="✅", member=MagicMock(bot=False), message_id=31337))
        self.assertIs(log.get(record_id).label, Label.YOU)
        await runner.on_raw_reaction_add(MagicMock(emoji="❌", member=MagicMock(bot=False), message_id=31337))
        self.assertIs(log.get(record_id).label, Label.NOT_YOU)
        mark_read.assert_not_called()

        # ❌ anywhere else means nothing.
        await runner.on_raw_reaction_add(MagicMock(emoji="❌", member=MagicMock(bot=False), channel_id=77,
                                                   message_id=31338))
        mark_read.assert_not_called()

    async def test_a_mark_on_a_team_radio_line_labels_the_route_first(self, mock_bot_class, mock_loader_class,
                                                                      mark_read, record_reply):
        from core.channel.discord import radio
        seen = []

        async def team_radio(message_id, emoji):
            seen.append((message_id, emoji))
            return message_id == "424242"

        radio.on_reaction(team_radio)
        try:
            # Team radio needs no voice room.
            runner, message = self._runner(mock_bot_class, mock_loader_class, voice=False)
            runner.bot.get_channel.return_value = message.channel
            await runner.on_raw_reaction_add(MagicMock(emoji="✅", member=MagicMock(bot=False), message_id=424242))
            self.assertEqual(seen, [("424242", "✅")])
            mark_read.assert_not_called()
        finally:
            radio.clear_listeners()

    async def test_a_mark_on_a_proactive_post_is_its_outcome_and_still_finishes_the_row(self, mock_bot_class,
                                                                                        mock_loader_class, mark_read,
                                                                                        record_reply):
        import datetime
        import tempfile
        import discord
        from core.channel.discord import radio
        from core.proactive.outcomes import Outcomes
        from core.storage.signals import SignalStore
        posted = datetime.datetime(2026, 10, 22, 6, 0, tzinfo=datetime.timezone.utc)
        message_id = discord.utils.time_snowflake(posted)
        with tempfile.TemporaryDirectory() as tmp:
            store = SignalStore(f"{tmp}/memory.db")
            decision_id = store.start_decision(tap="effect", session_id="s1", first_turn_at=None, mode="live")
            signal_id = store.add_signal({"decision_id": decision_id, "at": 1.0, "kind": "artifact",
                                          "gist": "Groceries"})
            route_id = store.add_route({"signal_id": signal_id, "decision_id": decision_id, "to_agent": "meal-planner",
                                        "asked": "now", "urgency": "now", "verdict": "deliver"})
            store.approve_delivery(route_id, to_agent="meal-planner", channel="meal-planning", deliver_at=1.0)
            store.finish_delivery(route_id, "posted", at=posted.timestamp(), message_ids=[str(message_id)])
            radio.on_reaction(Outcomes(store, clock=lambda: posted.timestamp() + 60, record=lambda line: None).reacted)
            try:
                runner, message = self._runner(mock_bot_class, mock_loader_class)
                runner.bot.get_channel.return_value = message.channel
                await runner.on_raw_reaction_add(MagicMock(emoji="✅", member=MagicMock(bot=False), channel_id=77,
                                                           message_id=message_id))
                self.assertEqual(store.route(route_id)["outcome"], "engaged")
                mark_read.assert_called_once()  # and the ✅ still finishes the post's inbox row, as before
            finally:
                radio.clear_listeners()

    async def test_a_poll_vote_reaches_team_radio_but_not_the_bots_own(self, mock_bot_class, mock_loader_class,
                                                                       mark_read, record_reply):
        from core.channel.discord import radio
        votes = []

        async def team_radio(message_id, answer_id):
            votes.append((message_id, answer_id))
            return True

        radio.on_poll_vote(team_radio)
        try:
            runner, _ = self._runner(mock_bot_class, mock_loader_class)
            runner.bot.user = MagicMock(id=1)
            await runner.on_raw_poll_vote_add(MagicMock(user_id=2, message_id=555, answer_id=2))
            await runner.on_raw_poll_vote_add(MagicMock(user_id=1, message_id=555, answer_id=3))
            self.assertEqual(votes, [("555", 2)])
        finally:
            radio.clear_listeners()


if __name__ == "__main__":
    unittest.main()
