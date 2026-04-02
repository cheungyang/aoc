import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import main
import discord

class TestThreadStarterBugFix(unittest.IsolatedAsyncioTestCase):

    @patch('main.session_manager')
    @patch('main.bot')
    @patch('main.isinstance')
    async def test_thread_starter_is_fetched_and_appended(self, mock_isinstance, mock_bot, mock_session_manager):
        # 1. Setup mock session manager
        # Simulate empty history (cold boot thread session)
        mock_session_manager.load_history.side_effect = [
            [], # First check before starter fetch recovery
            [{"ts": 1, "from": "human", "message": "Original main channel prompt context"}, {"ts": 2, "from": "human", "message": "Reply text"}] # After refreshes
        ]
        
        # 2. Setup mock message with Thread channel
        mock_message = MagicMock()
        mock_message.author = MagicMock(name="author")
        mock_message.author.name = "human_user"
        mock_message.content = "Reply text"
        
        mock_thread = MagicMock()
        mock_thread.id = 1489381180439531530
        mock_thread.name = "thread-name"
        mock_thread.send = AsyncMock()
        
        # Mock parent text channel
        mock_parent_channel = AsyncMock()
        mock_parent_channel.name = "general"
        mock_starter_msg_obj = MagicMock()
        mock_starter_msg_obj.content = "Original main channel prompt context"
        mock_starter_msg_obj.author.id = 99999 # Human author
        mock_parent_channel.fetch_message.return_value = mock_starter_msg_obj
        
        # Mock bot user id to be different
        mock_bot.user.id = 12345
        
        mock_thread.parent = mock_parent_channel
        mock_message.channel = mock_thread

        # Mock bot graph runner
        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {"messages": [MagicMock(content="Agent Reply text")]}
        mock_bot.graph = mock_graph

        # Patch isinstance to intercept discord.Thread identification mocks
        orig_isinstance = isinstance
        def side_effect_isinstance(obj, types):
             if obj is mock_thread and types is discord.Thread:
                  return True
             return orig_isinstance(obj, types)
        mock_isinstance.side_effect = side_effect_isinstance

        # Invoke handler
        await main.on_message(mock_message)

        # 4. Assertions
        # Verify it attempted to fetch the parent starter source message
        mock_parent_channel.fetch_message.assert_called_once_with(1489381180439531530)
        
        # Verify it appended the starter text
        mock_session_manager.append_message.assert_any_call(
            "discord:general:1489381180439531530", 
            "human", 
            "Original main channel prompt context"
        )
        
        # Verify it appended the current reply prompt segment
        mock_session_manager.append_message.assert_any_call(
            "discord:general:1489381180439531530", 
            "human", 
            "Reply text"
        )

    @patch('main.session_manager')
    @patch('main.bot')
    @patch('main.isinstance')
    async def test_thread_starter_from_agent_is_appended_as_agent(self, mock_isinstance, mock_bot, mock_session_manager):
        # Setup similar mocks but pointing author to Bot
        mock_session_manager.load_history.side_effect = [[], [
             {"ts": 1, "from": "agent", "message": "Bot greeting prompt"},
             {"ts": 2, "from": "human", "message": "Reply text"}
        ]]
        
        mock_message = MagicMock()
        mock_message.author = MagicMock()
        mock_message.author.name = "human_user"
        mock_message.content = "Reply text"
        
        mock_thread = MagicMock()
        mock_thread.id = 1489382995184980019
        mock_thread.name = "general"
        mock_thread.send = AsyncMock()
        
        mock_parent_channel = AsyncMock()
        mock_parent_channel.name = "general"
        mock_starter_msg_obj = MagicMock()
        mock_starter_msg_obj.content = "Bot greeting prompt"
        mock_starter_msg_obj.author = MagicMock()
        mock_starter_msg_obj.author.id = 12345 # Bot author
        mock_parent_channel.fetch_message.return_value = mock_starter_msg_obj
        
        mock_thread.parent = mock_parent_channel
        mock_message.channel = mock_thread

        mock_graph = AsyncMock()
        mock_graph.ainvoke.return_value = {"messages": [MagicMock(content="Agent Reply text")]}
        mock_bot.graph = mock_graph
        mock_bot.user = MagicMock()
        mock_bot.user.id = 12345 # Match Bot author 

        orig_isinstance = isinstance
        def side_effect_isinstance(obj, types):
             if obj is mock_thread and types is discord.Thread:
                  return True
             return orig_isinstance(obj, types)
        mock_isinstance.side_effect = side_effect_isinstance

        await main.on_message(mock_message)

        # Asserts role assigned to Agent
        mock_session_manager.append_message.assert_any_call(
            "discord:general:1489382995184980019", 
            "agent", 
            "Bot greeting prompt"
        )
        
        mock_session_manager.append_message.assert_any_call(
            "discord:general:1489382995184980019", 
            "human", 
            "Reply text"
        )

    @patch('main.session_manager')
    @patch('main.bot')
    @patch('main.isinstance')
    async def test_void_trigger_aborts_graph_runner(self, mock_isinstance, mock_bot, mock_session_manager):
        # Setup mocks simulating void creation bubble
        mock_message = MagicMock()
        mock_message.content = "" # Void content trigger
        
        mock_thread = MagicMock()
        mock_thread.send = AsyncMock()
        mock_message.channel = mock_thread
        
        mock_graph = AsyncMock()
        mock_bot.graph = mock_graph

        await main.on_message(mock_message)

        # Asserts loop aborted before invoking graph
        mock_graph.ainvoke.assert_not_called()

if __name__ == "__main__":
    unittest.main()
