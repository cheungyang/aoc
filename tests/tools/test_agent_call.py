import unittest
from unittest.mock import patch, MagicMock, AsyncMock, ANY
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.agent_call import agent_call
from core.util import format_tool_response
from core.agent.session_identifier import SessionIdentifier

class TestAgentCallTool(unittest.IsolatedAsyncioTestCase):

    @patch('core.loaders.bots_loader.BotsLoader')
    @patch('tools.agent_call.AgentsLoader')
    @patch('tools.agent_call.SessionIdentifier.new_job_id')
    async def test_agent_call_success(self, mock_get_job_id, mock_agents_loader, mock_bots_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channels": ["software-dev"]}
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.execute = AsyncMock(return_value="agent response")
        mock_get_job_id.return_value = "20260829_215134_abc12"
        
        mock_discord_channel = MagicMock()
        mock_bots_loader.return_value.find_channel.return_value = mock_discord_channel
        
        result = await agent_call.ainvoke({"agent_id": "agent1", "prompt": "hello", "channel": "software-dev"})
        
        mock_loader.get_agent.assert_called_once_with("agent1")
        mock_bots_loader.return_value.find_channel.assert_called_once_with("software-dev")
        mock_agent.execute.assert_called_once_with("hello", session=ANY)
        called_session = mock_agent.execute.call_args[1]["session"]
        self.assertEqual(called_session.agent_id, "agent1")
        self.assertEqual(called_session.source, "tool")
        self.assertEqual(called_session.job_id, "20260829_215134_abc12")
        self.assertEqual(called_session.channel_obj, mock_discord_channel)
        self.assertEqual(result, format_tool_response("agent_call", payload="agent response", errors="None"))

    @patch('core.loaders.bots_loader.BotsLoader')
    @patch('tools.agent_call.AgentsLoader')
    @patch('tools.agent_call.SessionIdentifier.new_job_id')
    async def test_agent_call_wildcard_channel(self, mock_get_job_id, mock_agents_loader, mock_bots_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channels": ["*"]}
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.execute = AsyncMock(return_value="agent response")
        mock_get_job_id.return_value = "job_123"
        
        mock_discord_channel = MagicMock()
        mock_bots_loader.return_value.find_channel.return_value = mock_discord_channel
        
        result = await agent_call.ainvoke({"agent_id": "main", "prompt": "hello", "channel": "any-channel"})
        
        mock_agent.execute.assert_called_once_with("hello", session=ANY)
        self.assertEqual(result, format_tool_response("agent_call", payload="agent response", errors="None"))

    @patch('tools.agent_call.AgentsLoader')
    async def test_agent_call_channel_restriction_error(self, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channels": ["software-dev"]}
        mock_loader.get_agent.return_value = mock_agent
        
        result = await agent_call.ainvoke({"agent_id": "scott", "prompt": "hello", "channel": "general"})
        
        self.assertIn("cannot be called in channel 'general'", result)
        self.assertIn("Allowed channels: ['software-dev']", result)

    @patch('core.loaders.bots_loader.BotsLoader')
    @patch('tools.agent_call.AgentsLoader')
    @patch('tools.agent_call.SessionIdentifier.new_job_id')
    async def test_agent_call_async(self, mock_get_job_id, mock_agents_loader, mock_bots_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channels": ["software-dev"]}
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.execute = AsyncMock(return_value="agent response")
        mock_get_job_id.return_value = "job_123"
        
        mock_discord_channel = MagicMock()
        mock_bots_loader.return_value.find_channel.return_value = mock_discord_channel
        
        result = await agent_call.ainvoke({"agent_id": "agent1", "prompt": "hello", "channel": "software-dev", "run_async": True})
        
        mock_loader.get_agent.assert_called_once_with("agent1")
        import asyncio
        await asyncio.sleep(0.1)
        mock_agent.execute.assert_called_once_with("hello", session=ANY)

        self.assertIn("Successfully triggered agent 'agent1'", result)

    @patch('core.loaders.bots_loader.BotsLoader')
    @patch('tools.agent_call.AgentsLoader')
    @patch('tools.agent_call.SessionIdentifier.new_job_id')
    async def test_agent_call_with_caller_param(self, mock_get_job_id, mock_agents_loader, mock_bots_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channels": ["software-dev"]}
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.execute = AsyncMock(return_value="agent response")
        mock_get_job_id.return_value = "job_123"
        
        mock_discord_channel = MagicMock()
        mock_bots_loader.return_value.find_channel.return_value = mock_discord_channel
        
        result = await agent_call.ainvoke({
            "agent_id": "agent1",
            "prompt": "hello",
            "channel": "software-dev",
            "caller": "main"
        })
        
        mock_agent.execute.assert_called_once_with(
            "<caller>main</caller>\nhello",
            session=ANY
        )
        self.assertEqual(result, format_tool_response("agent_call", payload="agent response", errors="None"))

    @patch('core.loaders.bots_loader.BotsLoader')
    @patch('tools.agent_call.AgentsLoader')
    @patch('tools.agent_call.SessionIdentifier.new_job_id')
    async def test_agent_call_with_contextvar_caller(self, mock_get_job_id, mock_agents_loader, mock_bots_loader):
        from core.agent.job_manager import current_session_identifier
        
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channels": ["software-dev"]}
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.execute = AsyncMock(return_value="agent response")
        mock_get_job_id.return_value = "job_123"
        
        mock_discord_channel = MagicMock()
        mock_bots_loader.return_value.find_channel.return_value = mock_discord_channel
        
        from core.agent.session_manager import SessionManager
        sess = SessionManager.get_session(agent_id="software-orchestrator", source="discord", channel="software-dev")
        token = current_session_identifier.set(sess)
        try:
            result = await agent_call.ainvoke({
                "agent_id": "agent1",
                "prompt": "hello",
                "channel": "software-dev"
            })
            
            mock_agent.execute.assert_called_once_with(
                "<caller>software-orchestrator</caller>\nhello",
                session=ANY
            )
            self.assertEqual(result, format_tool_response("agent_call", payload="agent response", errors="None"))
        finally:
            current_session_identifier.reset(token)

    @patch('core.loaders.bots_loader.BotsLoader')
    @patch('tools.agent_call.AgentsLoader')
    @patch('tools.agent_call.SessionIdentifier.new_job_id')
    async def test_agent_call_does_not_duplicate_caller_tag(self, mock_get_job_id, mock_agents_loader, mock_bots_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channels": ["software-dev"]}
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.execute = AsyncMock(return_value="agent response")
        mock_get_job_id.return_value = "job_123"
        
        mock_discord_channel = MagicMock()
        mock_bots_loader.return_value.find_channel.return_value = mock_discord_channel
        
        result = await agent_call.ainvoke({
            "agent_id": "agent1",
            "prompt": "<caller>custom_caller</caller>\nhello",
            "channel": "software-dev",
            "caller": "main"
        })
        
        mock_agent.execute.assert_called_once_with(
            "<caller>custom_caller</caller>\nhello",
            session=ANY
        )
        self.assertEqual(result, format_tool_response("agent_call", payload="agent response", errors="None"))

    @patch('core.loaders.bots_loader.BotsLoader')
    @patch('tools.agent_call.AgentsLoader')
    @patch('tools.agent_call.SessionIdentifier.new_job_id')
    async def test_agent_call_in_thread_preserves_thread_context(self, mock_get_job_id, mock_agents_loader, mock_bots_loader):
        import discord
        from core.agent.job_manager import current_session_identifier
        from core.agent.session_manager import SessionManager
        
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channels": ["topic-research"]}
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.execute = AsyncMock(return_value="agent thread response")
        mock_get_job_id.return_value = "job_456"
        
        # Mock active channel as a Thread
        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.id = 1541110915540324533
        mock_thread.name = "AI thread"
        mock_thread.parent = MagicMock(spec=discord.TextChannel)
        mock_thread.parent.name = "topic-research"
        
        caller_sess = SessionManager.get_session(agent_id="test-caller", source="discord", channel=mock_thread)
        token = current_session_identifier.set(caller_sess)
        try:
            result = await agent_call.ainvoke({
                "agent_id": "topic-researcher",
                "prompt": "explain deliberate practice",
                "channel": "topic-research"
            })
            
            # Should have used the active mock_thread directly instead of calling find_channel
            mock_bots_loader.return_value.find_channel.assert_not_called()
            mock_agent.execute.assert_called_once_with(
                "<caller>test-caller</caller>\nexplain deliberate practice",
                session=ANY
            )
            called_session = mock_agent.execute.call_args[1]["session"]
            self.assertEqual(called_session.agent_id, "topic-researcher")
            self.assertEqual(called_session.channel_name, "topic-research")
            self.assertEqual(called_session.discord_thread_id, "1541110915540324533")
            self.assertEqual(called_session.channel_obj, mock_thread)
            self.assertEqual(result, format_tool_response("agent_call", payload="agent thread response", errors="None"))
        finally:
            current_session_identifier.reset(token)

    @patch('core.loaders.bots_loader.BotsLoader')
    @patch('tools.agent_call.AgentsLoader')
    @patch('tools.agent_call.SessionIdentifier.new_job_id')
    async def test_agent_call_in_channel_preserves_channel_context(self, mock_get_job_id, mock_agents_loader, mock_bots_loader):
        import discord
        from core.agent.job_manager import current_session_identifier
        from core.agent.session_manager import SessionManager
        
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channels": ["topic-research"]}
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.execute = AsyncMock(return_value="agent channel response")
        mock_get_job_id.return_value = "job_789"
        
        # Mock active channel as a TextChannel
        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.id = 12345
        mock_channel.name = "topic-research"
        mock_channel.parent = None
        
        caller_sess = SessionManager.get_session(agent_id="test-caller", source="discord", channel=mock_channel)
        token = current_session_identifier.set(caller_sess)
        try:
            result = await agent_call.ainvoke({
                "agent_id": "topic-researcher",
                "prompt": "explain naive practice",
                "channel": "topic-research"
            })
            
            mock_bots_loader.return_value.find_channel.assert_not_called()
            mock_agent.execute.assert_called_once_with(
                "<caller>test-caller</caller>\nexplain naive practice",
                session=ANY
            )
            called_session = mock_agent.execute.call_args[1]["session"]
            self.assertEqual(called_session.agent_id, "topic-researcher")
            self.assertEqual(called_session.channel_name, "topic-research")
            self.assertEqual(called_session.channel_obj, mock_channel)
            self.assertEqual(result, format_tool_response("agent_call", payload="agent channel response", errors="None"))
        finally:
            current_session_identifier.reset(token)

    async def test_missing_args(self):
        with self.assertRaises(Exception):
            await agent_call.ainvoke({"agent_id": "agent1"})

    async def test_missing_channel_arg(self):
        with self.assertRaises(Exception):
            await agent_call.ainvoke({"agent_id": "agent1", "prompt": "hello"})


if __name__ == '__main__':
    unittest.main()
