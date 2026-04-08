import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.agent import Agent

class TestAgent(unittest.IsolatedAsyncioTestCase):

    @patch('core.agent.agent.HookLoader')
    @patch('core.debug_handler.DebugLogHandler')
    @patch('core.util.get_session_id')
    async def test_process_message_success(self, mock_get_session_id, mock_debug_handler_class, mock_hook_loader_class):
        # Setup mocks
        mock_hook_loader = MagicMock()
        mock_pre_hook = AsyncMock()
        mock_post_hook = AsyncMock()
        mock_hook_loader.pre_message_hooks = [mock_pre_hook]
        mock_hook_loader.post_message_hooks = [mock_post_hook]
        mock_hook_loader_class.return_value = mock_hook_loader
        
        mock_get_session_id.return_value = "session1"
        mock_debug_handler = MagicMock()
        mock_debug_handler_class.return_value = mock_debug_handler

        mock_message = MagicMock()
        mock_message.channel.send = AsyncMock()
        mock_bot = MagicMock()
        
        # Graph invoke result
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Reply text")]})

        agent = Agent(mock_graph)
        
        # Run
        await agent.process_message(mock_message, mock_bot)
        
        # Assertions
        mock_pre_hook.assert_called_once_with("session1", mock_message.content)
        mock_graph.ainvoke.assert_called_once()
        mock_post_hook.assert_called_once_with("session1", "Reply text")
        mock_message.channel.send.assert_called_once_with("Reply text")

    

    @patch('core.agent.agent.HookLoader')
    @patch('core.debug_handler.DebugLogHandler')
    @patch('core.util.get_session_id')
    async def test_process_message_invoke_failure(self, mock_get_session_id, mock_debug_handler_class, mock_hook_loader_class):
        # Setup mocks
        mock_hook_loader = MagicMock()
        mock_hook_loader.pre_message_hooks = []
        mock_hook_loader_class.return_value = mock_hook_loader
        
        mock_message = MagicMock()
        mock_message.channel.send = AsyncMock()
        mock_bot = MagicMock()
        
        # Graph invoke throws exception
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(side_effect=Exception("Invoke failed"))

        agent = Agent(mock_graph)
        
        # Run
        await agent.process_message(mock_message, mock_bot)
        
        # Assertions
        mock_message.channel.send.assert_called_once_with("Sorry, I encountered an error processing the request.")

    @patch('core.agent.agent.HookLoader')
    @patch('core.debug_handler.DebugLogHandler')
    @patch('core.util.get_session_id')
    async def test_process_message_parsing_failure(self, mock_get_session_id, mock_debug_handler_class, mock_hook_loader_class):
        # Setup mocks
        mock_hook_loader = MagicMock()
        mock_hook_loader.pre_message_hooks = []
        mock_hook_loader_class.return_value = mock_hook_loader
        
        mock_message = MagicMock()
        mock_message.channel.send = AsyncMock()
        mock_bot = MagicMock()
        
        # Graph invoke succeeds but returns empty messages list (causing IndexError)
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": []})

        agent = Agent(mock_graph)
        
        # Run and Expect IndexError (not caught by Agent's loop)
        with self.assertRaises(IndexError):
            await agent.process_message(mock_message, mock_bot)

    @patch('core.agent.agent.HookLoader')
    @patch('core.debug_handler.DebugLogHandler')
    @patch('core.util.get_session_id')
    async def test_process_message_long_text(self, mock_get_session_id, mock_debug_handler_class, mock_hook_loader_class):
        # Setup mocks
        mock_hook_loader = MagicMock()
        mock_hook_loader.pre_message_hooks = []
        mock_hook_loader.post_message_hooks = []
        mock_hook_loader_class.return_value = mock_hook_loader
        
        mock_get_session_id.return_value = "session1"
        
        mock_message = MagicMock()
        mock_message.channel.send = AsyncMock()
        mock_bot = MagicMock()
        
        # Graph invoke result with long text
        long_text = "a" * 3000
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content=long_text)]})

        agent = Agent(mock_graph)
        
        # Run
        await agent.process_message(mock_message, mock_bot)
        
        # Assertions
        # Should be called twice since limit is 2000 and text is 3000
        self.assertEqual(mock_message.channel.send.call_count, 2)
        mock_message.channel.send.assert_any_call("a" * 2000)
        mock_message.channel.send.assert_any_call("a" * 1000)

    @patch('core.agent.agent.HookLoader')
    @patch('core.debug_handler.DebugLogHandler')
    @patch('core.util.get_session_id')
    @patch('core.agent.agent.ReactionCallbackHandler')
    async def test_process_message_passes_callback(self, mock_reaction_handler_class, mock_get_session_id, mock_debug_handler_class, mock_hook_loader_class):
        # Setup mocks
        mock_hook_loader = MagicMock()
        mock_hook_loader.pre_message_hooks = []
        mock_hook_loader.post_message_hooks = []
        mock_hook_loader_class.return_value = mock_hook_loader
        
        mock_get_session_id.return_value = "session1"
        
        mock_reaction_handler = MagicMock()
        mock_reaction_handler_class.return_value = mock_reaction_handler
        
        mock_message = MagicMock()
        mock_message.channel.send = AsyncMock()
        mock_bot = MagicMock()
        
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Reply text")]})

        agent = Agent(mock_graph)
        
        # Run
        await agent.process_message(mock_message, mock_bot)
        
        # Assertions
        mock_reaction_handler_class.assert_called_once_with(mock_message)
        
        # Verify that callback was passed to ainvoke
        args, kwargs = mock_graph.ainvoke.call_args
        config = kwargs.get("config")
        callbacks = config.get("callbacks")
        self.assertIn(mock_reaction_handler, callbacks)

class TestReactionCallbackHandler(unittest.IsolatedAsyncioTestCase):

    @patch('core.agent.agents_loader.AgentsLoader')
    async def test_on_tool_start_delegation(self, mock_agents_loader_class):
        mock_agents_loader = MagicMock()
        mock_agents_loader.get_agent_config.return_value = {"emoji": "🤖"}
        mock_agents_loader_class.return_value = mock_agents_loader

        mock_message = MagicMock()
        mock_message.add_reaction = AsyncMock()

        from core.agent.agent import ReactionCallbackHandler
        handler = ReactionCallbackHandler(mock_message)

        serialized = {"name": "agent_call"}
        input_str = '{"action": "launch_subagent", "agent_id": "test-agent"}'

        await handler.on_tool_start(serialized, input_str)

        mock_message.add_reaction.assert_called_once_with("🤖")

    @patch('core.agent.agents_loader.AgentsLoader')
    async def test_on_tool_start_single_quotes(self, mock_agents_loader_class):
        mock_agents_loader = MagicMock()
        mock_agents_loader.get_agent_config.return_value = {"emoji": "🤖"}
        mock_agents_loader_class.return_value = mock_agents_loader

        mock_message = MagicMock()
        mock_message.add_reaction = AsyncMock()

        from core.agent.agent import ReactionCallbackHandler
        handler = ReactionCallbackHandler(mock_message)

        serialized = {"name": "agent_call"}
        input_str = "{'action': 'launch_subagent', 'agent_id': 'test-agent'}"

        await handler.on_tool_start(serialized, input_str)

        mock_message.add_reaction.assert_called_once_with("🤖")

    @patch('core.agent.agents_loader.AgentsLoader')
    async def test_on_tool_start_other_action(self, mock_agents_loader_class):
        mock_message = MagicMock()
        mock_message.add_reaction = AsyncMock()

        from core.agent.agent import ReactionCallbackHandler
        handler = ReactionCallbackHandler(mock_message)

        serialized = {"name": "agent_call"}
        input_str = '{"action": "check_subagent", "job_id": "123"}'

        await handler.on_tool_start(serialized, input_str)

        mock_message.add_reaction.assert_not_called()

if __name__ == "__main__":
    unittest.main()
