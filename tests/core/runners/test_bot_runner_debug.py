import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

# Provide mock for discord if not installed
if 'discord' not in sys.modules:
    mock_discord = MagicMock()
    class MockThread:
        def __init__(self, *args, **kwargs):
            self.parent = None
            self.name = ""
            self.id = ""
        def typing(self):
            pass
        def history(self, limit=2):
            pass
        async def send(self, *args, **kwargs):
            pass
    mock_discord.Thread = MockThread
    sys.modules['discord'] = mock_discord
    sys.modules['discord.ext'] = MagicMock()
    sys.modules['discord.ext.commands'] = MagicMock()
    sys.modules['discord.ui'] = MagicMock()
    sys.modules['mcp'] = MagicMock()
    sys.modules['mcp.client'] = MagicMock()
    sys.modules['mcp.client.stdio'] = MagicMock()
    sys.modules['langchain_mcp_adapters'] = MagicMock()
    sys.modules['langchain_mcp_adapters.tools'] = MagicMock()

import discord
from core.runners.bot_runner import BotRunner
from core.util.config import Config

class TestBotRunnerDebugMode(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.config = Config()
        self.config.reset()
        self.runner = BotRunner("test_token", "main")
        self.runner.bot = MagicMock()
        self.runner.bot.user = MagicMock()
        self.runner.bot.user.bot = True

    def tearDown(self):
        self.config.reset()

    @patch('core.runners.bot_runner.AgentsLoader')
    async def test_debug_mode_disabled_processes_normal_channels(self, mock_agents_loader):
        self.config.is_debug = False
        self.config.debug_channel = "debug-channel"

        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": ["general"]}
        mock_agent.execute = AsyncMock(return_value="reply")
        mock_loader.get_agent.return_value = mock_agent

        mock_message = MagicMock()
        mock_message.author.bot = False
        mock_message.channel.name = "general"
        mock_message.channel.id = "111"
        mock_message.channel.parent = None
        mock_message.mentions = []
        mock_message.content = "hello"
        mock_message.attachments = []

        await self.runner.on_message(mock_message)

        mock_agent.execute.assert_called_once()

    @patch('core.runners.bot_runner.AgentsLoader')
    async def test_debug_mode_disabled_ignores_debug_channel(self, mock_agents_loader):
        self.config.is_debug = False
        self.config.debug_channel = "debug-channel"

        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": ["debug-channel"]}
        mock_agent.execute = AsyncMock(return_value="reply")
        mock_loader.get_agent.return_value = mock_agent

        # Message sent in debug-channel when is_debug == False
        mock_message = MagicMock()
        mock_message.author.bot = False
        mock_message.channel.name = "debug-channel"
        mock_message.channel.id = "999"
        mock_message.channel.parent = None
        mock_message.mentions = [self.runner.bot.user]
        mock_message.content = "hello in debug channel"

        await self.runner.on_message(mock_message)

        # Agent should NOT be called because debug is False and channel is debug_channel
        mock_agents_loader.assert_not_called()
        mock_agent.execute.assert_not_called()

    @patch('core.runners.bot_runner.AgentsLoader')
    async def test_debug_mode_enabled_ignores_non_debug_channel(self, mock_agents_loader):
        self.config.is_debug = True
        self.config.debug_channel = "debug-channel"

        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": ["general"]}
        mock_agent.execute = AsyncMock(return_value="reply")
        mock_loader.get_agent.return_value = mock_agent

        # Message in 'general' channel
        mock_message = MagicMock()
        mock_message.author.bot = False
        mock_message.channel.name = "general"
        mock_message.channel.id = "111"
        mock_message.channel.parent = None
        mock_message.mentions = [self.runner.bot.user]
        mock_message.content = "hello"

        await self.runner.on_message(mock_message)

        # Agent should NOT be called because channel is not debug-channel
        mock_agents_loader.assert_not_called()
        mock_agent.execute.assert_not_called()

    @patch('core.runners.bot_runner.AgentsLoader')
    async def test_debug_mode_enabled_responds_in_debug_channel(self, mock_agents_loader):
        self.config.is_debug = True
        self.config.debug_channel = "debug-channel"

        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": ["debug-channel"]}
        mock_agent.execute = AsyncMock(return_value="reply")
        mock_loader.get_agent.return_value = mock_agent

        # Message in 'debug-channel'
        mock_message = MagicMock()
        mock_message.author.bot = False
        mock_message.channel.name = "debug-channel"
        mock_message.channel.id = "999"
        mock_message.channel.parent = None
        mock_message.mentions = []
        mock_message.content = "hello in debug"
        mock_message.attachments = []

        await self.runner.on_message(mock_message)

        # Agent should be executed
        mock_agent.execute.assert_called_once()

    @patch('core.runners.bot_runner.AgentsLoader')
    async def test_debug_mode_enabled_thread_in_debug_channel(self, mock_agents_loader):
        self.config.is_debug = True
        self.config.debug_channel = "debug-channel"

        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": ["debug-channel"]}
        mock_agent.execute = AsyncMock(return_value="reply")
        mock_loader.get_agent.return_value = mock_agent

        parent_channel = MagicMock()
        parent_channel.name = "debug-channel"
        parent_channel.id = "999"

        # Thread inside 'debug-channel'
        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.name = "sub-thread"
        mock_thread.id = "888"
        mock_thread.parent = parent_channel
        mock_thread.typing.return_value.__aenter__ = AsyncMock()
        mock_thread.typing.return_value.__aexit__ = AsyncMock()
        
        async def async_history(limit=2):
            if False:
                yield None
        mock_thread.history = MagicMock(side_effect=lambda limit=2: async_history(limit))

        mock_message = MagicMock()
        mock_message.author.bot = False
        mock_message.channel = mock_thread
        mock_message.mentions = []
        mock_message.content = "hello in thread"
        mock_message.attachments = []

        await self.runner.on_message(mock_message)

        # Agent should be executed
        mock_agent.execute.assert_called_once()

    @patch('core.runners.bot_runner.AgentsLoader')
    async def test_debug_mode_enabled_thread_in_non_debug_channel(self, mock_agents_loader):
        self.config.is_debug = True
        self.config.debug_channel = "debug-channel"

        mock_loader = MagicMock()
        mock_agents_loader.return_value = mock_loader
        mock_agent = MagicMock()
        mock_agent.config = {"channel_hosts": ["general"]}
        mock_agent.execute = AsyncMock(return_value="reply")
        mock_loader.get_agent.return_value = mock_agent

        parent_channel = MagicMock()
        parent_channel.name = "general"
        parent_channel.id = "111"

        # Thread inside 'general'
        mock_thread = MagicMock(spec=discord.Thread)
        mock_thread.name = "sub-thread"
        mock_thread.id = "888"
        mock_thread.parent = parent_channel

        mock_message = MagicMock()
        mock_message.author.bot = False
        mock_message.channel = mock_thread
        mock_message.mentions = [self.runner.bot.user]
        mock_message.content = "hello in thread"

        await self.runner.on_message(mock_message)

        # Agent should NOT be executed
        mock_agent.execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
