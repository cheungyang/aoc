import unittest
import os
import sys
from unittest.mock import MagicMock, patch

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.session_manager import SessionManager
from core.agent.session_identifier import SessionIdentifier


class TestSessionManager(unittest.TestCase):

    def test_get_session_standard(self):
        message = MagicMock()
        message.channel.name = "general"
        message.channel.id = 123
        
        session = SessionManager.get_session("agent1", "discord", message.channel)
        self.assertIsInstance(session, SessionIdentifier)
        self.assertEqual(session.session_id, "agent1:discord:general")

    def test_get_session_access_session_id(self):
        # Accessing session_id via get_session().session_id
        session_id = SessionManager.get_session("agent1", "discord", "general").session_id
        self.assertEqual(session_id, "agent1:discord:general")

    def test_get_session_thread(self):
        import discord
        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.id = 456
        mock_thread.parent = MagicMock()
        mock_thread.parent.name = "general"
        
        session = SessionManager.get_session("agent1", "discord", mock_thread)
        self.assertEqual(session.session_id, "agent1:discord:general:456")
        self.assertTrue(session.is_thread())

    def test_get_session_tool_channel(self):
        import discord
        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.name = "topic-research"
        mock_channel.id = 12345
        mock_channel.parent = None
        
        session = SessionManager.get_session("topic-researcher", "tool", mock_channel)
        self.assertEqual(session.session_id, "topic-researcher:tool:topic-research")

    def test_get_session_tool_thread(self):
        import discord
        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.id = 1541110915540324533
        mock_thread.name = "AI thread"
        mock_thread.parent = MagicMock(spec=discord.TextChannel)
        mock_thread.parent.name = "topic-research"
        
        session = SessionManager.get_session("topic-researcher", "tool", mock_thread)
        self.assertEqual(session.session_id, "topic-researcher:tool:topic-research:1541110915540324533")

    @patch('core.agent.session_manager.SqliteSessionStore')
    def test_clear_session(self, mock_store_class):
        mock_store = MagicMock()
        mock_store.archive_session.return_value = "Archived"
        mock_store_class.return_value = mock_store
        
        ident = SessionManager.get_session("main", source="discord", channel="general")
        result = SessionManager().clear_session(ident)
        self.assertEqual(result, "Archived")
        mock_store.archive_session.assert_called_once_with("main:discord:general")

    @patch('core.agent.session_manager.SqliteSessionStore')
    def test_clear_sessions(self, mock_store_class):
        mock_store = MagicMock()
        mock_store.archive_all_sessions.return_value = "Archived all"
        mock_store_class.return_value = mock_store
        
        result = SessionManager().clear_sessions()
        self.assertEqual(result, "Archived all")
        mock_store.archive_all_sessions.assert_called_once()


if __name__ == '__main__':
    unittest.main()
