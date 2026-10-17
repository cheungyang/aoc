"""set_thinking: the agent's own pick, for one model call, capped on voice."""
import unittest
from unittest.mock import MagicMock, patch

from core.runtime.execution_context import current_execution_context
from core.runtime.session_manager import SessionManager
from core.runtime.thinking import PICK_KEY, pick_thinking, resolve_thinking_level
from core.runtime.turn_trace import detach_turn_trace, start_turn_trace
from core.util.models import GOOGLE_TIERS

FLASH = GOOGLE_TIERS["FLASH"]
PRO = GOOGLE_TIERS["PRO"]


class ThinkingCase(unittest.TestCase):
    def setUp(self):
        loader = MagicMock()
        loader.get_agent_config.side_effect = lambda aid: self.configs.get(aid, {})
        self.configs = {"coach": {}}
        patcher = patch("core.loaders.agents_loader.AgentsLoader", return_value=loader)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(detach_turn_trace)

    def bind(self, surface="text", **kw):
        session = SessionManager.get_session(agent_id="coach", source="discord", channel="fitness",
                                             surface=surface, **kw)
        token = current_execution_context.set(session)
        self.addCleanup(current_execution_context.reset, token)
        return session


class TestPick(ThinkingCase):
    def test_a_pick_covers_the_next_call_only(self):
        self.bind("text")
        self.assertEqual(pick_thinking("high", "many constraints"), "high")
        self.assertEqual(resolve_thinking_level(FLASH), "high")
        self.assertEqual(resolve_thinking_level(FLASH), "medium", "back to the text default")

    def test_voice_picks_are_capped(self):
        self.bind("voice")
        self.assertEqual(pick_thinking("high", "math"), "medium")
        self.assertEqual(resolve_thinking_level(FLASH), "medium")
        self.assertEqual(resolve_thinking_level(FLASH), "low")

    def test_a_pick_below_the_cap_is_kept_on_voice(self):
        self.bind("voice")
        self.assertEqual(pick_thinking("minimal", "relaying"), "minimal")
        self.assertEqual(resolve_thinking_level(FLASH), "minimal")

    def test_a_pick_beats_the_schedule_level_and_is_still_clamped(self):
        self.bind("text", thinking_level="high")
        pick_thinking("minimal", "just relaying")
        self.assertEqual(resolve_thinking_level(PRO), "low", "PRO has no minimal")
        self.assertEqual(resolve_thinking_level(PRO), "high", "then the schedule's level again")

    def test_unknown_levels_are_refused(self):
        self.bind()
        with self.assertRaises(ValueError):
            pick_thinking("max", "x")

    def test_the_pick_and_its_reason_go_in_the_turn_trace(self):
        self.bind("voice")
        trace = start_turn_trace("coach", 1)
        pick_thinking("high", "choosing among 12 exercises")
        self.assertEqual(
            [e for e in trace.events if e["kind"] == "thinking_pick"][0],
            {"t": trace.events[0]["t"], "kind": "thinking_pick", "agent": "coach",
             "level": "high", "applied": "medium", "reason": "choosing among 12 exercises"},
        )

    def test_another_agents_context_starts_without_the_pick(self):
        session = self.bind("text")
        pick_thinking("high", "x")
        self.assertNotIn(PICK_KEY, session.with_agent("helper").turn_hints)
        self.assertIn(PICK_KEY, session.with_surface("voice").turn_hints, "same agent, same turn")


class TestTool(ThinkingCase):
    def test_the_tool_reports_the_cap(self):
        from tools.set_thinking import set_thinking

        self.bind("voice")
        out = set_thinking.invoke({"level": "high", "reason": "math"})
        self.assertIn("Next call: medium (asked for high; voice turns cap at medium)", out)

    def test_the_tool_outside_a_run_is_an_error(self):
        from tools.set_thinking import set_thinking

        out = set_thinking.invoke({"level": "low", "reason": "x"})
        self.assertIn("Error", out)


if __name__ == "__main__":
    unittest.main()
