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
        import asyncio
        mock_agents_loader = MagicMock()
        mock_agents_loader.get_agent.return_value.config = {"emoji": "🤖"}
        mock_agents_loader_class.return_value = mock_agents_loader

        # Create a mock message with a separate dummy running event loop
        other_loop = MagicMock(spec=asyncio.AbstractEventLoop)
        other_loop.is_running.return_value = True

        mock_message = MagicMock()
        mock_state = MagicMock()
        mock_state.loop = other_loop
        mock_message._state = mock_state
        mock_message.add_reaction = MagicMock()

        with patch('asyncio.run_coroutine_threadsafe') as mock_threadsafe:
            handler = ReactionCallbackHandler(mock_message)
            serialized = {"name": "agent_call"}
            input_str = '{"agent_id": "test-agent", "prompt": "hello"}'

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
