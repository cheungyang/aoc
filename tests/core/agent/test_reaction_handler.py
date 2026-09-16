import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.reaction_handler import ReactionCallbackHandler

class TestReactionCallbackHandler(unittest.IsolatedAsyncioTestCase):

    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_on_tool_start_delegation(self, mock_agents_loader_class):
        mock_agents_loader = MagicMock()
        mock_agents_loader.get_agent.return_value.config = {"emoji": "🤖"}
        mock_agents_loader_class.return_value = mock_agents_loader

        mock_message = MagicMock()
        mock_message.add_reaction = AsyncMock()

        handler = ReactionCallbackHandler(mock_message)

        serialized = {"name": "agent_call"}
        input_str = '{"agent_id": "test-agent", "prompt": "hello"}'

        await handler.on_tool_start(serialized, input_str)

        mock_message.add_reaction.assert_called_once_with("🤖")

    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_on_tool_start_single_quotes(self, mock_agents_loader_class):
        mock_agents_loader = MagicMock()
        mock_agents_loader.get_agent.return_value.config = {"emoji": "🤖"}
        mock_agents_loader_class.return_value = mock_agents_loader

        mock_message = MagicMock()
        mock_message.add_reaction = AsyncMock()

        handler = ReactionCallbackHandler(mock_message)

        serialized = {"name": "agent_call"}
        input_str = "{'agent_id': 'test-agent', 'prompt': 'hello'}"

        await handler.on_tool_start(serialized, input_str)

        mock_message.add_reaction.assert_called_once_with("🤖")

    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_on_tool_start_dict_input(self, mock_agents_loader_class):
        mock_agents_loader = MagicMock()
        mock_agents_loader.get_agent.return_value.config = {"emoji": "🚀"}
        mock_agents_loader_class.return_value = mock_agents_loader

        mock_message = MagicMock()
        mock_message.add_reaction = AsyncMock()

        handler = ReactionCallbackHandler(mock_message)

        serialized = {"name": "agent_call"}
        input_dict = {"agent_id": "test-agent", "prompt": "hello"}

        await handler.on_tool_start(serialized, input_dict)

        mock_message.add_reaction.assert_called_once_with("🚀")

    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_on_tool_start_cross_loop(self, mock_agents_loader_class):
        """
        The callback can be fired from a worker thread running its own event loop
        (LangChain executor) while the discord.Message belongs to the bot's loop.
        Awaiting the reaction directly would run it on the wrong loop, so
        _add_reaction_safe must marshal it with asyncio.run_coroutine_threadsafe
        (reaction_handler.py:40-43). This drives that exact path for real.
        """
        import asyncio
        import threading

        mock_agents_loader = MagicMock()
        mock_agents_loader.get_agent.return_value.config = {"emoji": "🤖"}
        mock_agents_loader_class.return_value = mock_agents_loader

        # The message belongs to *this* loop (stand-in for the discord bot loop).
        message_loop = asyncio.get_running_loop()
        reacted = asyncio.Event()
        reaction_loops = []

        async def fake_add_reaction(emoji):
            reaction_loops.append(asyncio.get_running_loop())
            reacted.set()

        mock_message = MagicMock()
        mock_state = MagicMock()
        mock_state.loop = message_loop
        mock_message._state = mock_state
        mock_message.add_reaction = AsyncMock(side_effect=fake_add_reaction)

        handler = ReactionCallbackHandler(mock_message)
        serialized = {"name": "agent_call"}
        input_str = '{"agent_id": "test-agent", "prompt": "hello"}'

        # Fire the callback from a different thread, on a different event loop.
        worker_errors = []

        def worker():
            try:
                asyncio.run(handler.on_tool_start(serialized, input_str))
            except BaseException as e:  # noqa: BLE001 - surfaced as a test failure below
                worker_errors.append(e)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        # The reaction must actually be executed, and on the message's loop.
        await asyncio.wait_for(reacted.wait(), timeout=5)
        thread.join(timeout=5)

        self.assertEqual(worker_errors, [])
        self.assertFalse(thread.is_alive())
        mock_message.add_reaction.assert_awaited_once_with("🤖")
        self.assertEqual(reaction_loops, [message_loop])

    @patch('core.loaders.graphs_loader.GraphsLoader')
    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_on_tool_start_graph_call_non_main(self, mock_agents_loader_class, mock_graphs_loader_class):
        mock_graphs_loader = MagicMock()
        mock_graphs_loader.get_graph_config.return_value = {"graph_id": "coding", "emoji": "💻"}
        mock_graphs_loader_class.return_value = mock_graphs_loader

        mock_message = MagicMock()
        mock_message.add_reaction = AsyncMock()

        handler = ReactionCallbackHandler(mock_message)

        serialized = {"name": "graph_call"}
        input_str = '{"graph_name": "coding", "query": "write tests"}'

        await handler.on_tool_start(serialized, input_str)

        mock_message.add_reaction.assert_called_once_with("💻")

    @patch('core.loaders.graphs_loader.GraphsLoader')
    @patch('core.loaders.agents_loader.AgentsLoader')
    async def test_on_tool_start_graph_call_main_ignored(self, mock_agents_loader_class, mock_graphs_loader_class):
        mock_graphs_loader = MagicMock()
        mock_graphs_loader.get_graph_config.return_value = {"graph_id": "main", "emoji": "🧭"}
        mock_graphs_loader_class.return_value = mock_graphs_loader

        mock_message = MagicMock()
        mock_message.add_reaction = AsyncMock()

        handler = ReactionCallbackHandler(mock_message)

        serialized = {"name": "graph_call"}
        input_str = '{"graph_name": "main", "query": "hello"}'

        await handler.on_tool_start(serialized, input_str)

        mock_message.add_reaction.assert_not_called()


if __name__ == "__main__":
    unittest.main()
