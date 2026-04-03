import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import skills.sessions.hooks.startup as startup
import discord

# Save real isinstance to avoid recursion crash loops inside MagicMock implementations
real_isinstance = isinstance

class TestThreadStarterBugFix(unittest.IsolatedAsyncioTestCase):

    @patch('skills.sessions.hooks.startup.session_manager')
    async def test_thread_starter_is_fetched_and_appended(self, mock_session_manager):
        mock_session_manager.get_file_path.return_value = "mock_path.json"
        mock_session_manager.load_history.side_effect = [[], [{"ts": 1, "from": "human", "message": "Original main channel prompt context"}]]

        mock_message = MagicMock()
        mock_message.content = "Reply text"
        
        mock_thread = MagicMock()
        mock_thread.id = 1489381180439531530
        
        mock_parent_channel = AsyncMock()
        mock_parent_channel.name = "general"
        mock_starter_msg_obj = MagicMock()
        mock_starter_msg_obj.content = "Original main channel prompt context"
        mock_starter_msg_obj.author.id = 99999 # Human author
        mock_parent_channel.fetch_message.return_value = mock_starter_msg_obj
        
        mock_thread.parent = mock_parent_channel
        mock_message.channel = mock_thread

        mock_bot = MagicMock()
        mock_bot.user.id = 12345

        def custom_isinstance(obj, types):
             if obj is mock_thread and types is discord.Thread:
                  return True
             return real_isinstance(obj, types)

        with patch('builtins.isinstance', new=custom_isinstance):
             result = await startup.hook_pre_message(mock_message, mock_bot)

        mock_parent_channel.fetch_message.assert_called_once_with(1489381180439531530)
        mock_session_manager.append_message.assert_any_call(
            "discord:general:1489381180439531530", 
            "human", 
            "Original main channel prompt context"
        )

    @patch('skills.sessions.hooks.startup.session_manager')
    async def test_thread_starter_from_agent_is_appended_as_agent(self, mock_session_manager):
        mock_session_manager.get_file_path.return_value = "mock_path.json"
        mock_session_manager.load_history.side_effect = [[], [{"ts": 1, "from": "agent", "message": "Bot greeting prompt"}]]

        mock_message = MagicMock()
        mock_message.content = "Reply text"
        
        mock_thread = MagicMock()
        mock_thread.id = 1489382995184980019
        
        mock_parent_channel = AsyncMock()
        mock_parent_channel.name = "general"
        mock_starter_msg_obj = MagicMock()
        mock_starter_msg_obj.content = "Bot greeting prompt"
        mock_starter_msg_obj.author.id = 12345 # Bot author
        mock_parent_channel.fetch_message.return_value = mock_starter_msg_obj
        
        mock_thread.parent = mock_parent_channel
        mock_message.channel = mock_thread

        mock_bot = MagicMock()
        mock_bot.user.id = 12345

        def custom_isinstance(obj, types):
             if obj is mock_thread and types is discord.Thread:
                  return True
             return real_isinstance(obj, types)

        with patch('builtins.isinstance', new=custom_isinstance):
             await startup.hook_pre_message(mock_message, mock_bot)

        mock_session_manager.append_message.assert_any_call(
            "discord:general:1489382995184980019", 
            "agent", 
            "Bot greeting prompt"
        )

    @patch('skills.sessions.hooks.startup.session_manager')
    async def test_void_trigger_aborts_hook(self, mock_session_manager):
        mock_message = MagicMock()
        mock_message.content = "" # Void content trigger
        
        mock_bot = MagicMock()

        result = await startup.hook_pre_message(mock_message, mock_bot)
        self.assertIsNone(result)

if __name__ == "__main__":
    unittest.main()
