import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.loaders.bots_loader import BotsLoader

class TestBotsLoader(unittest.TestCase):

    def setUp(self):
        # Clear singleton cache
        loader = BotsLoader()
        loader._bots = {}

    @patch('core.loaders.bots_loader.AgentsLoader')
    @patch('core.loaders.bots_loader.BotRunner')
    @patch('os.getenv')
    def test_get_bot(self, mock_getenv, mock_bot_runner, mock_agents_loader):
        loader = BotsLoader()
        
        mock_agents = MagicMock()
        mock_agents_loader.return_value = mock_agents
        
        mock_agent = MagicMock()
        mock_agent.get_config.return_value = "TOKEN_KEY"
        mock_agents.get_agent.return_value = mock_agent
        
        mock_getenv.return_value = "valid_token"
        
        mock_bot = MagicMock()
        mock_bot_runner.return_value = mock_bot
        
        bot = loader.get_bot("test-agent")
        
        self.assertIs(bot, mock_bot)
        mock_bot_runner.assert_called_once_with("valid_token", "test-agent")
        
        # Test caching
        bot2 = loader.get_bot("test-agent")
        self.assertIs(bot2, mock_bot)
        self.assertEqual(mock_bot_runner.call_count, 1)

    @patch('core.loaders.bots_loader.AgentsLoader')
    def test_get_bot_missing_token_key(self, mock_agents_loader):
        loader = BotsLoader()
        
        mock_agents = MagicMock()
        mock_agents_loader.return_value = mock_agents
        
        mock_agent = MagicMock()
        mock_agent.get_config.return_value = None
        mock_agents.get_agent.return_value = mock_agent
        
        bot = loader.get_bot("test-no-token-key")
        self.assertIsNone(bot)

    @patch('core.loaders.bots_loader.AgentsLoader')
    @patch('os.getenv')
    def test_get_bot_invalid_token(self, mock_getenv, mock_agents_loader):
        loader = BotsLoader()
        
        mock_agents = MagicMock()
        mock_agents_loader.return_value = mock_agents
        
        mock_agent = MagicMock()
        mock_agent.get_config.return_value = "TOKEN_KEY"
        mock_agents.get_agent.return_value = mock_agent
        
        mock_getenv.return_value = None
        
        bot = loader.get_bot("test-no-env-token")
        self.assertIsNone(bot)

    def test_find_channel_found_by_name(self):
        loader = BotsLoader()
        
        mock_ch = MagicMock()
        mock_ch.name = "software-dev"
        mock_ch.id = 12345
        
        mock_guild = MagicMock()
        mock_guild.text_channels = [mock_ch]
        
        mock_bot_runner = MagicMock()
        mock_bot_runner.bot.guilds = [mock_guild]
        
        loader._bots = {"main": mock_bot_runner}
        
        found = loader.find_channel("software-dev")
        self.assertEqual(found, mock_ch)

    def test_find_channel_found_by_id(self):
        loader = BotsLoader()
        
        mock_ch = MagicMock()
        mock_ch.name = "software-dev"
        mock_ch.id = 12345
        
        mock_guild = MagicMock()
        mock_guild.text_channels = [mock_ch]
        
        mock_bot_runner = MagicMock()
        mock_bot_runner.bot.guilds = [mock_guild]
        
        loader._bots = {"main": mock_bot_runner}
        
        found = loader.find_channel("12345")
        self.assertEqual(found, mock_ch)

    def test_find_channel_not_found(self):
        loader = BotsLoader()
        
        mock_ch = MagicMock()
        mock_ch.name = "software-dev"
        mock_ch.id = 12345
        
        mock_guild = MagicMock()
        mock_guild.text_channels = [mock_ch]
        
        mock_bot_runner = MagicMock()
        mock_bot_runner.bot.guilds = [mock_guild]
        
        loader._bots = {"main": mock_bot_runner}
        
        found = loader.find_channel("general")
        self.assertIsNone(found)

if __name__ == '__main__':
    unittest.main()
