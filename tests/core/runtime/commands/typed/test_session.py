"""The session commands, typed to the bot: !new, !newall, !restart and
!compact, answered by the bot that answers the channel."""
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.runtime.commands.base import TypedContext
from core.runtime.commands.registry import TYPED
from core.runtime.foundation.sessions import SessionManager


def typed(channel, answers_here=True):
    """Where `!...` was typed: `channel`, on the session a message there runs on."""
    session = SessionManager.get_session(agent_id="test", source="discord", channel=channel)
    return TypedContext(channel=session.channel_obj, agent_id="test", session=session, answers_here=answers_here)


def _channel():
    channel = AsyncMock()
    channel.name = "general"
    return channel


class TestSessionCommands(unittest.IsolatedAsyncioTestCase):

    async def test_a_message_that_is_not_a_command_is_left_alone(self):
        for text in ("regular user prompt", "[new]", "!nope"):
            self.assertIsNone(await TYPED.answer(text, typed("general")), text)

    @patch('core.runtime.foundation.sessions.SessionManager.clear_session')
    async def test_new_clears_this_channels_session(self, mock_clear_session):
        mock_clear_session.return_value = "archived table"
        channel = _channel()
        ctx = typed(channel)
        self.assertIsNotNone(await TYPED.answer("!new", ctx))
        mock_clear_session.assert_called_once_with(ctx.session)
        channel.send.assert_called_once_with("Session context cleared. archived table")

    @patch('core.runtime.foundation.sessions.SessionManager.clear_session')
    async def test_new_without_a_channel_to_answer_in(self, mock_clear_session):
        mock_clear_session.return_value = "archived table"
        ctx = typed("general")
        self.assertIsNotNone(await TYPED.answer("!new", ctx))
        mock_clear_session.assert_called_once_with(ctx.session)

    @patch('core.runtime.foundation.sessions.SessionManager.clear_session')
    async def test_only_the_bot_that_answers_the_channel_answers(self, mock_clear_session):
        channel = _channel()
        self.assertIsNone(await TYPED.answer("!new", typed(channel, answers_here=False)))
        mock_clear_session.assert_not_called()
        channel.send.assert_not_called()

    @patch('core.runtime.foundation.sessions.SessionManager.clear_sessions')
    async def test_newall_clears_every_session(self, mock_clear_sessions):
        mock_clear_sessions.return_value = "all archived"
        channel = _channel()
        self.assertIsNotNone(await TYPED.answer("!newall", typed(channel)))
        mock_clear_sessions.assert_called_once()
        channel.send.assert_called_once_with("All session contexts cleared. all archived")

    @patch('core.runtime.foundation.sessions.SessionManager.clear_sessions')
    async def test_newall_without_a_channel_to_answer_in(self, mock_clear_sessions):
        mock_clear_sessions.return_value = "all archived"
        self.assertIsNotNone(await TYPED.answer("!newall", typed("general")))
        mock_clear_sessions.assert_called_once()

    @patch('core.runtime.commands.typed.restart.os.execv')
    async def test_restart(self, mock_execv):
        channel = _channel()
        self.assertIsNotNone(await TYPED.answer("!restart", typed(channel)))
        channel.send.assert_called_once_with("System is restarting...")
        mock_execv.assert_called_once_with(sys.executable, [sys.executable] + sys.argv)

    @patch('core.runtime.commands.typed.restart.os.execv')
    async def test_restart_without_a_channel_to_answer_in(self, mock_execv):
        self.assertIsNotNone(await TYPED.answer("!restart", typed("general")))
        mock_execv.assert_called_once_with(sys.executable, [sys.executable] + sys.argv)

    @patch('core.runtime.history.pruner.ContextPruner._asummarize_with_graph_worker', return_value="Compacted summary")
    @patch('core.knowledge.memory.sqlite_checkpointer.SqliteCheckpointer.put')
    @patch('core.knowledge.memory.sqlite_checkpointer.SqliteCheckpointer.get_tuple')
    async def test_compact(self, mock_get_tuple, mock_put, mock_worker):
        from langchain_core.messages import HumanMessage
        from core.config.settings import Config
        Config().context_window_messages = 5
        mock_tuple = MagicMock()
        mock_tuple.config = {"configurable": {"thread_id": "test:discord:general"}}
        mock_tuple.metadata = {}
        mock_tuple.checkpoint = {
            "channel_values": {
                "messages": [
                    HumanMessage(content="A" * 500) for _ in range(20)
                ]
            }
        }
        mock_get_tuple.return_value = mock_tuple
        channel = _channel()

        self.assertIsNotNone(await TYPED.answer("!compact", typed(channel)))
        mock_put.assert_called_once()
        channel.send.assert_called()
        sent_text = channel.send.call_args[0][0]
        self.assertIn("Session Context Compacted", sent_text)

    @patch('core.knowledge.memory.sqlite_checkpointer.SqliteCheckpointer.get_tuple', return_value=None)
    async def test_summarize_is_compact(self, _mock_get_tuple):
        channel = _channel()
        self.assertIsNotNone(await TYPED.answer("!summarize", typed(channel)))
        channel.send.assert_called_once_with("No active checkpoint messages found to compact.")


if __name__ == "__main__":
    unittest.main()
