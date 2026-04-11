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
    async def test_on_tool_start_missing_agent_id(self, mock_agents_loader_class):
        mock_message = MagicMock()
        mock_message.add_reaction = AsyncMock()

        handler = ReactionCallbackHandler(mock_message)

        serialized = {"name": "agent_call"}
        input_str = '{"prompt": "hello"}'

        await handler.on_tool_start(serialized, input_str)

        mock_message.add_reaction.assert_not_called()


if __name__ == "__main__":
    unittest.main()
