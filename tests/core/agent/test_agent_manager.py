import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.agent_manager import AgentManager
from core.agent.reaction_handler import ReactionCallbackHandler

class TestAgentManager(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.manager = AgentManager()
        self.mock_loader = MagicMock()
        self.manager.loader = self.mock_loader

    def test_set_get_status(self):
        self.manager.set_agent_status("agent1", "online")
        self.assertEqual(self.manager.get_agent_status("agent1"), "online")
        self.assertEqual(self.manager.get_agent_status("agent2"), "offline")

    def test_list_online_agents(self):
        self.manager.set_agent_status("agent1", "online")
        self.manager.set_agent_status("agent2", "offline")
        self.manager.set_agent_status("agent3", "online")
        self.assertEqual(set(self.manager.list_online_agents()), {"agent1", "agent3"})

    def test_is_bot_host(self):
        self.mock_loader.get_agent_config.return_value = {"discord_token_key": "KEY"}
        self.assertTrue(self.manager.is_bot_host("agent1"))
        
        self.mock_loader.get_agent_config.return_value = {}
        self.assertFalse(self.manager.is_bot_host("agent2"))

    async def test_trigger_agent(self):
        mock_agent = AsyncMock()
        mock_agent.execute.return_value = "Reply text"
        self.mock_loader.get_agent = AsyncMock(return_value=mock_agent)
        
        callbacks = [MagicMock()]
        result = await self.manager.trigger_agent("agent1", prompt="prompt", session_id="session1", callbacks=callbacks)
        
        self.mock_loader.get_agent.assert_called_once_with("agent1")
        mock_agent.execute.assert_called_once_with("prompt", "session1", callbacks=callbacks)
        self.assertEqual(result, "Reply text")

if __name__ == "__main__":
    unittest.main()
