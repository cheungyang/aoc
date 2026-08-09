import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tools.agent_call import agent_call
from core.util import format_tool_response

class TestAgentCallTool(unittest.IsolatedAsyncioTestCase):

    @patch('core.loaders.bots_loader.BotsLoader')
    @patch('tools.agent_call.AgentsLoader')
    @patch('core.agent.job_manager.JobManager.new_job_id')
    async def test_agent_call_success(self, mock_get_job_id, mock_agents_loader, mock_bots_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channels": ["software-dev"]}
        mock_loader.get_agent.return_value = mock_agent
        mock_agent.execute = AsyncMock(return_value="agent response")
        mock_get_job_id.return_value = "job_123"
        
        mock_discord_channel = MagicMock()
        mock_bots_loader.return_value.find_channel.return_value = mock_discord_channel
        
        result = await agent_call.ainvoke({"agent_id": "agent1", "prompt": "hello", "channel": "software-dev"})
        
        mock_loader.get_agent.assert_called_once_with("agent1")
        mock_bots_loader.return_value.find_channel.assert_called_once_with("software-dev")
        mock_agent.execute.assert_called_once_with("hello", source="tool", job_id="job_123", channel=mock_discord_channel)
        self.assertEqual(result, format_tool_response("agent_call", payload="agent response", errors="None"))

    @patch('core.loaders.bots_loader.BotsLoader')
    @patch('tools.agent_call.AgentsLoader')
    @patch('core.agent.job_manager.JobManager.new_job_id')
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
        
        mock_agent.execute.assert_called_once_with("hello", source="tool", job_id="job_123", channel=mock_discord_channel)
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
    @patch('core.agent.job_manager.JobManager.new_job_id')
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
        mock_agent.execute.assert_called_once_with("hello", source="tool", job_id="job_123", channel=mock_discord_channel)

        self.assertIn("Successfully triggered agent 'agent1'", result)

    async def test_missing_args(self):
        with self.assertRaises(Exception):
            await agent_call.ainvoke({"agent_id": "agent1"})

    async def test_missing_channel_arg(self):
        with self.assertRaises(Exception):
            await agent_call.ainvoke({"agent_id": "agent1", "prompt": "hello"})


if __name__ == '__main__':
    unittest.main()
