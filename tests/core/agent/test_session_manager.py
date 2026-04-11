import unittest
import os
import sys
import datetime
from unittest.mock import MagicMock, patch

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.session_manager import SessionManager

class TestSessionManager(unittest.TestCase):
    
    @patch('core.agent.session_manager.FlatFileSessionStore')
    def test_clear_session(self, mock_store_class):
        mock_store = MagicMock()
        mock_store.archive_session.return_value = "Archived"
        mock_store_class.return_value = mock_store
        
        result = SessionManager().clear_session("test_session")
        self.assertEqual(result, "Archived")
        mock_store.archive_session.assert_called_once_with("test_session")

    @patch('core.agent.session_manager.SessionManager.clear_session')
    @patch('glob.glob')
    @patch('os.path.exists')
    @patch('core.agent.session_manager.FlatFileSessionStore')
    def test_clear_sessions(self, mock_store_class, mock_exists, mock_glob, mock_clear_session):
        mock_store = MagicMock()
        mock_store.sessions_dir = "/dummy/sessions"
        mock_store_class.return_value = mock_store
        
        mock_exists.return_value = True
        mock_glob.return_value = ["/dummy/sessions/session1.jsonl", "/dummy/sessions/session2.jsonl"]
        
        SessionManager().clear_sessions()
        
        self.assertEqual(mock_clear_session.call_count, 2)
        mock_clear_session.assert_any_call("session1")
        mock_clear_session.assert_any_call("session2")

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
