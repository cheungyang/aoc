import unittest
import os
import sys
import datetime
from unittest.mock import MagicMock, patch

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.session_manager import SessionManager

class TestSessionManager(unittest.TestCase):
    
    @patch('core.agent.session_manager.SqliteSessionStore')
    def test_clear_session(self, mock_store_class):
        mock_store = MagicMock()
        mock_store.archive_session.return_value = "Archived"
        mock_store_class.return_value = mock_store
        
        result = SessionManager().clear_session("test_session")
        self.assertEqual(result, "Archived")
        mock_store.archive_session.assert_called_once_with("test_session")

    @patch('core.agent.session_manager.SqliteSessionStore')
    def test_clear_sessions(self, mock_store_class):
        mock_store = MagicMock()
        mock_store.archive_all_sessions.return_value = "Archived all"
        mock_store_class.return_value = mock_store
        
        result = SessionManager().clear_sessions()
        self.assertEqual(result, "Archived all")
        mock_store.archive_all_sessions.assert_called_once()

    def test_get_session_id_standard(self):
        message = MagicMock()
        message.channel.name = "general"
        message.channel.id = 123
        
        session_id = SessionManager().get_session_id("agent1", "discord", message.channel)
        self.assertEqual(session_id, "agent1:discord:general")

    def test_get_session_id_thread(self):
        import discord
        message = MagicMock()
        message.channel = MagicMock(spec=discord.Thread)
        message.channel.id = 456
        message.channel.parent.name = "general"
        
        session_id = SessionManager().get_session_id("agent1", "discord", message.channel)
        self.assertEqual(session_id, "agent1:discord:general:456")

if __name__ == '__main__':
    unittest.main()
