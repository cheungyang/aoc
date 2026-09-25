"""Memory v2 runtime plumbing: non-recording calls and per-call model floors."""
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.runtime.agent import Agent
from core.runtime.execution_context import current_execution_context, inherited_record_memory
from core.runtime.session_manager import SessionManager
from core.util.models import DEFAULT_AGENT_TIER, tier_floor


class TestTierFloor(unittest.TestCase):

    def test_raises_weaker_tiers_to_the_floor(self):
        self.assertEqual(tier_floor("FLASH_LITE", "FLASH"), "FLASH")
        self.assertEqual(tier_floor("flash-lite", "FLASH"), "FLASH")

    def test_keeps_stronger_tiers(self):
        self.assertEqual(tier_floor("PRO", "FLASH"), "PRO")
        self.assertEqual(tier_floor("FLASH", "FLASH"), "FLASH")

    def test_unset_means_the_default_tier(self):
        self.assertEqual(tier_floor(None, "FLASH_LITE"), DEFAULT_AGENT_TIER)

    def test_a_pinned_model_id_is_not_second_guessed(self):
        self.assertEqual(tier_floor("gemini-2.5-flash-lite", "FLASH"), "gemini-2.5-flash-lite")


class TestRecordMemory(unittest.TestCase):

    def test_sessions_record_by_default(self):
        session = SessionManager.get_session("a", "discord", "general")
        self.assertTrue(session.record_memory)
        self.assertIsNone(session.model)

    def test_a_non_recording_call_stays_non_recording_downstream(self):
        outer = SessionManager.get_session("a", "discord", "general", record_memory=False, model="FLASH")
        self.assertEqual(outer.model, "FLASH")
        token = current_execution_context.set(outer)
        try:
            self.assertFalse(inherited_record_memory())
            inner = SessionManager.get_session("b", "tool", "general", record_memory=True)
            self.assertFalse(inner.record_memory)
        finally:
            current_execution_context.reset(token)
        self.assertTrue(inherited_record_memory())

    def test_the_model_override_belongs_to_one_agent(self):
        session = SessionManager.get_session("a", "discord", "general", model="PRO")
        self.assertIsNone(session.with_agent("b").model)

    @patch("core.runtime.agent.save_agent_memory_log")
    def test_agent_saves_logs_only_when_recording(self, save):
        raw = "hi<system_memory_log>- learned</system_memory_log>"
        agent = Agent("a", {})
        agent._parse_final_response(raw, SessionManager.get_session("a", "discord", "general", record_memory=False))
        save.assert_not_called()
        Agent("a", {"stateless": True})._parse_final_response(raw)
        save.assert_not_called()
        agent._parse_final_response(raw, SessionManager.get_session("a", "discord", "general"))
        save.assert_called_once()


class TestModelOverrideGraphCache(unittest.IsolatedAsyncioTestCase):

    async def test_a_model_override_compiles_its_own_graph(self):
        agent = Agent("a", {})
        builder = MagicMock()
        builder.return_value.build_graph = AsyncMock(side_effect=lambda ctx, cfg: f"graph:{ctx.model}")
        with patch("core.runtime.graph_builder.GraphBuilder", builder):
            plain = await agent._get_graph(SessionManager.get_session("a", "discord", "general"))
            floored = await agent._get_graph(SessionManager.get_session("a", "discord", "general", model="FLASH"))
            again = await agent._get_graph(SessionManager.get_session("a", "discord", "general"))
        self.assertEqual((plain, floored, again), ("graph:None", "graph:FLASH", "graph:None"))
        self.assertEqual(sorted(agent._graphs), ["", "|FLASH"])


if __name__ == "__main__":
    unittest.main()
