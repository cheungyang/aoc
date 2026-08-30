import unittest
import os
import sys
import discord
from unittest.mock import MagicMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.session_identifier import SessionIdentifier
from core.agent.session_manager import SessionManager


class TestSessionIdentifier(unittest.TestCase):
    
    def test_direct_instantiation_raises_runtime_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            SessionIdentifier(agent_id="test-agent", source="discord", channel="general")
        self.assertIn("Direct instantiation of SessionIdentifier is prohibited", str(ctx.exception))

    def test_session_identifier_creation_via_session_manager(self):
        ident = SessionManager.get_session(agent_id="test-agent", source="discord", channel="general")
        self.assertEqual(ident.agent_id, "test-agent")
        self.assertEqual(ident.source, "discord")
        self.assertEqual(ident.channel_name, "general")
        self.assertEqual(ident.session_id, "test-agent:discord:general")

    def test_session_identifier_immutability(self):
        ident = SessionManager.get_session(agent_id="test-agent", source="discord", channel="general")
        with self.assertRaises(Exception):
            ident.agent_id = "new-agent"

    def test_session_identifier_validation(self):
        with self.assertRaises(ValueError):
            SessionManager.get_session(agent_id="", source="discord", channel="general")
        with self.assertRaises(ValueError):
            SessionManager.get_session(agent_id="test", source="", channel="general")
        with self.assertRaises(ValueError):
            SessionManager.get_session(agent_id="test", source="discord", channel=None, stateless=False)

    def test_session_identifier_stateless_job_format(self):
        ident = SessionManager.get_session(agent_id="worker", job_id="20260829_215134_abc12", source="job", stateless=True)
        self.assertEqual(ident.get_session_id(), "worker:job:20260829_215134_abc12")
        self.assertEqual(ident.session_id, "worker:job:20260829_215134_abc12")
        self.assertTrue(ident.is_stateless())

    def test_session_identifier_auto_job_generation(self):
        ident = SessionManager.get_session(agent_id="topic-researcher", source="job")
        self.assertTrue(ident.is_stateless())
        self.assertIsNotNone(ident.job_id)
        self.assertEqual(len(ident.job_id), 8)
        self.assertFalse("job" in ident.job_id)
        self.assertEqual(ident.get_session_id(), f"topic-researcher:job:{ident.job_id}")

    def test_session_identifier_from_channel(self):
        import discord
        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.name = "general"
        mock_channel.id = 1001

        ident = SessionManager.get_session(agent_id="main", source="discord", channel=mock_channel)
        self.assertEqual(ident.get_session_id(), "main:discord:general")
        self.assertEqual(ident.channel_name, "general")
        self.assertEqual(ident.get_channel_obj(), mock_channel)
        self.assertFalse(ident.is_thread())

    def test_session_identifier_from_thread(self):
        import discord
        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.id = 2002
        mock_thread.name = "sub-topic"
        mock_thread.parent = MagicMock(spec=discord.TextChannel)
        mock_thread.parent.name = "topic-research"

        ident = SessionManager.get_session(agent_id="topic-researcher", source="tool", channel=mock_thread)
        self.assertEqual(ident.get_session_id(), "topic-researcher:tool:topic-research:2002")
        self.assertEqual(ident.channel_name, "topic-research")
        self.assertEqual(ident.discord_thread_id, "2002")
        self.assertEqual(ident.get_discord_thread_id(), "2002")
        self.assertEqual(ident.get_channel_obj(), mock_thread)
        self.assertTrue(ident.is_thread())

    def test_session_identifier_from_context(self):
        ident = SessionManager.get_session(agent_id="main", source="discord", channel="general")
        self.assertEqual(ident.get_session_id(), "main:discord:general")

    def test_session_identifier_new_job_id(self):
        job_id = SessionIdentifier.new_job_id()
        self.assertEqual(len(job_id), 8)
        self.assertIsInstance(job_id, str)
        self.assertNotEqual(job_id, SessionIdentifier.new_job_id())

    def test_matches_channel(self):
        # 1. Text channel match
        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.id = 12345
        mock_channel.name = "coding-pipeline"
        mock_channel.parent = None
        ident = SessionManager.get_session(agent_id="test", source="discord", channel=mock_channel)
        self.assertTrue(ident.matches_channel("coding-pipeline"))
        self.assertTrue(ident.matches_channel("#coding-pipeline"))
        self.assertTrue(ident.matches_channel("12345"))
        self.assertFalse(ident.matches_channel("general"))
        self.assertFalse(ident.matches_channel(""))

        # 2. Thread match
        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.id = 9999
        mock_thread.name = "Bug Discussion"
        mock_thread.parent = mock_channel
        ident_th = SessionManager.get_session(agent_id="test", source="discord", channel=mock_thread)
        self.assertTrue(ident_th.matches_channel("Bug Discussion"))
        self.assertTrue(ident_th.matches_channel("9999"))
        self.assertTrue(ident_th.matches_channel("coding-pipeline"))
        self.assertFalse(ident_th.matches_channel("other"))

        # 3. String channel match
        ident_str = SessionManager.get_session(agent_id="test", source="discord", channel="general")
        self.assertTrue(ident_str.matches_channel("general"))
        self.assertTrue(ident_str.matches_channel("#general"))
        self.assertFalse(ident_str.matches_channel("dev"))

    def test_get_session_thread_id(self):
        # 1. Main agent execution (no graph_id or 'main')
        ident_main = SessionManager.get_session(agent_id="main", source="discord", channel="general")
        self.assertEqual(ident_main.get_session_thread_id(), "main:discord:general")
        self.assertEqual(ident_main.get_session_thread_id("main"), "main:discord:general")
        self.assertEqual(ident_main.get_session_thread_id(None), "main:discord:general")

        # 2. Subgraph execution
        self.assertEqual(ident_main.get_session_thread_id("coding"), "coding:main:discord:general")
        self.assertEqual(ident_main.get_session_thread_id("content_creation"), "content_creation:main:discord:general")


if __name__ == "__main__":
    unittest.main()
