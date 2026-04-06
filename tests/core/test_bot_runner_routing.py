import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.bot_runner import BotRunner

class TestBotRunnerRouting(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.runner = BotRunner("test_token", "agent-designer")
        self.runner.bot = MagicMock()
        self.runner.bot.user = MagicMock() # Mock the bot user

    @patch('core.agents_loader.AgentsLoader')
    @patch('core.agent_builder.AgentBuilder')
    async def test_on_message_ignores_bots(self, mock_agent_builder, mock_agents_loader):
        mock_message = MagicMock()
        mock_message.author.bot = True
        
        await self.runner.on_message(mock_message)
        
        mock_agent_builder.assert_not_called()

    @patch('core.agents_loader.AgentsLoader')
    @patch('core.agent_builder.AgentBuilder')
    async def test_on_message_host_responds_without_tag(self, mock_agent_builder, mock_agents_loader):
        # Mock Loader
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent_config.return_value = {"channel_hosts": ["agent-lab"]}
        
        # Mock Message
        mock_message = MagicMock()
        mock_message.author.bot = False
        mock_message.channel.name = "agent-lab"
        mock_message.mentions = []
        mock_message.content = "hello"
        
        # Mock Agent
        mock_agent = MagicMock()
        mock_agent.process_message = AsyncMock()
        mock_agent_builder.return_value.build_agent = AsyncMock(return_value=mock_agent)

        await self.runner.on_message(mock_message)
        
        mock_agent.process_message.assert_called_once()

    @patch('core.agents_loader.AgentsLoader')
    @patch('core.agent_builder.AgentBuilder')
    async def test_on_message_host_yields_to_tagged(self, mock_agent_builder, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent_config.return_value = {"channel_hosts": ["agent-lab"]}
        
        mock_other_bot = MagicMock()
        mock_other_bot.bot = True
        
        mock_message = MagicMock()
        mock_message.author.bot = False
        mock_message.channel.name = "agent-lab"
        mock_message.mentions = [mock_other_bot]
        mock_message.content = "hello"
        
        await self.runner.on_message(mock_message)
        
        # Should yield, so process_message not called
        mock_agent_builder.return_value.build_agent.assert_not_called()

    @patch('core.agents_loader.AgentsLoader')
    @patch('core.agent_builder.AgentBuilder')
    async def test_on_message_non_host_ignores_untagged(self, mock_agent_builder, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent_config.return_value = {"channel_hosts": ["other-channel"]}
        
        mock_message = MagicMock()
        mock_message.author.bot = False
        mock_message.channel.name = "agent-lab" # Not in hosts
        mock_message.mentions = []
        mock_message.content = "hello"
        
        await self.runner.on_message(mock_message)
        
        mock_agent_builder.assert_not_called()

    @patch('core.agents_loader.AgentsLoader')
    @patch('core.agent_builder.AgentBuilder')
    async def test_on_message_non_host_responds_when_tagged(self, mock_agent_builder, mock_agents_loader):
        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_loader.get_agent_config.return_value = {"channel_hosts": ["other-channel"]}
        
        mock_message = MagicMock()
        mock_message.author.bot = False
        mock_message.channel.name = "agent-lab"
        mock_message.mentions = [self.runner.bot.user] # Tagged self
        mock_message.content = "hello"
        
        mock_agent = MagicMock()
        mock_agent.process_message = AsyncMock()
        mock_agent_builder.return_value.build_agent = AsyncMock(return_value=mock_agent)

        await self.runner.on_message(mock_message)
        
        mock_agent.process_message.assert_called_once()

if __name__ == '__main__':
    unittest.main()
