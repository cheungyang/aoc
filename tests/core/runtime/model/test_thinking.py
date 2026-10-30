"""Which thinking level each model call runs at (core.runtime.model.thinking)."""
import unittest
from unittest.mock import MagicMock, patch

from core.runtime.foundation.context import current_execution_context
from core.runtime.foundation.sessions import SessionManager
from core.runtime.model.thinking import PICK_KEY, MarkerFilter, pick_thinking, resolve_thinking_level, take_marker
from core.runtime.observability.turn_trace import detach_turn_trace, start_turn_trace
from core.config.models import FLASH, FLASH_LITE, PRO, THINKING_BY_SURFACE, clamp_thinking_level


def _ctx(agent_id="day-planner", **kw):
    kw.setdefault("source", "discord")
    kw.setdefault("channel", "day-planning")
    return SessionManager.get_session(agent_id=agent_id, **kw)


class ResolverCase(unittest.TestCase):
    def setUp(self):
        self.configs = {
            "day-planner": {"model": "FLASH"},
            "goal-setter": {"model": "PRO", "thinking": {"text": "high"}},
            "graph-worker-low": {"model": "FLASH_LITE", "stateless": True},
        }
        loader = patch("core.runtime.model.thinking.AgentsLoader").start()
        loader.return_value.get_agent_config.side_effect = self.configs.get
        self.addCleanup(patch.stopall)

    def resolve(self, ctx, model=FLASH):
        token = current_execution_context.set(ctx)
        try:
            return resolve_thinking_level(model)
        finally:
            current_execution_context.reset(token)


class TestDefaultsBySurface(ResolverCase):

    def test_code_defaults(self):
        self.assertEqual(THINKING_BY_SURFACE, {"voice": "low", "text": "medium", "scheduled": "high"})
        self.assertEqual(self.resolve(_ctx(surface="voice")), "low")
        self.assertEqual(self.resolve(_ctx()), "medium")  # a typed Discord turn is "text"
        self.assertEqual(self.resolve(_ctx(source="scheduled")), "high")

    def test_no_default_for_tool_and_job_surfaces(self):
        self.assertIsNone(self.resolve(_ctx(source="tool", stateless=True)))
        self.assertIsNone(self.resolve(_ctx(source="job")))

    def test_outside_an_agent_run_sends_nothing(self):
        self.assertIsNone(resolve_thinking_level(FLASH))


class TestOverrides(ResolverCase):

    def test_agent_override_for_its_surface_only(self):
        self.assertEqual(self.resolve(_ctx("goal-setter"), PRO), "high")
        self.assertEqual(self.resolve(_ctx("goal-setter", surface="voice"), PRO), "low")

    def test_schedule_level_beats_the_agent_and_the_default(self):
        ctx = _ctx("goal-setter", source="scheduled", thinking_level="medium")
        self.assertEqual(self.resolve(ctx, PRO), "medium")
        self.assertEqual(self.resolve(_ctx(source="scheduled", thinking_level="low")), "low")

    def test_stateless_workers_keep_the_model_default(self):
        """A worker's caller chose it for a cost point, model thinking included."""
        self.assertIsNone(self.resolve(_ctx("graph-worker-low", surface="voice", stateless=True), FLASH_LITE))

    def test_delegated_agents_inherit_the_surface_but_not_the_override(self):
        caller = _ctx("goal-setter", source="scheduled", thinking_level="minimal")
        callee = caller.with_agent("day-planner")
        self.assertIsNone(callee.thinking_level)
        self.assertEqual(self.resolve(callee), "high")

    def test_result_is_clamped_to_the_model(self):
        self.assertEqual(self.resolve(_ctx(source="scheduled", thinking_level="minimal"), PRO), "low")


class TestPick(ResolverCase):
    """The model's pick, from its <next_thinking> marker: its next call only, capped on voice."""

    def setUp(self):
        super().setUp()
        self.printed = patch("builtins.print").start()

    def bind(self, ctx):
        token = current_execution_context.set(ctx)
        self.addCleanup(current_execution_context.reset, token)
        return ctx

    def test_a_pick_covers_the_next_call_only(self):
        self.bind(_ctx())
        self.assertEqual(pick_thinking("high"), "high")
        self.assertEqual(resolve_thinking_level(FLASH), "high")
        self.assertEqual(resolve_thinking_level(FLASH), "medium", "back to the text default")

    def test_voice_picks_are_capped(self):
        self.bind(_ctx(surface="voice"))
        self.assertEqual(pick_thinking("high"), "medium")
        self.assertEqual(resolve_thinking_level(FLASH), "medium")
        self.assertEqual(resolve_thinking_level(FLASH), "low")

    def test_a_pick_below_the_cap_is_kept_on_voice(self):
        self.bind(_ctx(surface="voice"))
        self.assertEqual(pick_thinking("minimal"), "minimal")
        self.assertEqual(resolve_thinking_level(FLASH), "minimal")

    def test_a_pick_beats_the_schedule_level_and_is_still_clamped(self):
        self.bind(_ctx(thinking_level="high"))
        pick_thinking("minimal")
        self.assertEqual(resolve_thinking_level(PRO), "low", "PRO has no minimal")
        self.assertEqual(resolve_thinking_level(PRO), "high", "then the schedule's level again")

    def test_an_unknown_level_records_nothing(self):
        ctx = self.bind(_ctx())
        self.assertIsNone(pick_thinking("max"))
        self.assertNotIn(PICK_KEY, ctx.turn_hints)
        self.assertEqual(resolve_thinking_level(FLASH), "medium")

    def test_outside_an_agent_run_nothing_is_recorded(self):
        self.assertIsNone(pick_thinking("high"))

    def test_a_stateless_worker_keeps_the_model_default(self):
        self.bind(_ctx("graph-worker-low", stateless=True))
        pick_thinking("high")
        self.assertIsNone(resolve_thinking_level(FLASH_LITE))

    def test_the_pick_goes_in_the_log_and_the_voice_trace(self):
        trace = start_turn_trace("day-planner", 1)
        self.addCleanup(detach_turn_trace)
        self.bind(_ctx(surface="voice"))
        pick_thinking("high")
        self.printed.assert_called_once_with("[Thinking] agent=day-planner surface=voice picked=high applied=medium")
        events = [{k: v for k, v in e.items() if k != "t"} for e in trace.events if e["kind"] == "thinking_pick"]
        self.assertEqual(events, [{"kind": "thinking_pick", "agent": "day-planner", "level": "high", "applied": "medium"}])

    def test_another_agents_context_starts_without_the_pick(self):
        ctx = self.bind(_ctx())
        pick_thinking("high")
        self.assertNotIn(PICK_KEY, ctx.with_agent("helper").turn_hints)
        self.assertIn(PICK_KEY, ctx.with_surface("voice").turn_hints, "same agent, same turn")


class TestMarker(unittest.TestCase):
    """`<next_thinking>LEVEL</next_thinking>`, taken out of a response's text."""

    def test_a_whole_response(self):
        self.assertEqual(take_marker("<next_thinking>high</next_thinking>\nChecking the files."),
                         ("Checking the files.", "high"))
        self.assertEqual(take_marker("<Next_Thinking> Medium </Next_Thinking>"), ("", "medium"))
        self.assertEqual(take_marker("No marker here."), ("No marker here.", None))

    def test_a_marker_split_across_chunks(self):
        marker = MarkerFilter()
        out = [marker.feed(t) for t in ("<next_th", "inking>hi", "gh</next_thinking>\n", "Reading them now.")]
        self.assertEqual(out, ["", "", "", "Reading them now."])
        self.assertEqual(marker.level, "high")

    def test_other_text_goes_straight_out(self):
        marker = MarkerFilter()
        self.assertEqual(marker.feed("Here they are. <speech>Three picks.</speech>"),
                         "Here they are. <speech>Three picks.</speech>")
        self.assertEqual(marker.feed(" 2 < 3"), " 2 < 3")
        self.assertIsNone(marker.level)

    def test_a_look_alike_is_released_once_it_differs(self):
        marker = MarkerFilter()
        self.assertEqual(marker.feed("Use <next"), "Use ")
        self.assertEqual(marker.feed(" time"), "<next time")

    def test_a_start_still_held_at_the_end_is_flushed(self):
        marker = MarkerFilter()
        self.assertEqual(marker.feed("Done <next_"), "Done ")
        self.assertEqual(marker.flush(), "<next_")
        self.assertEqual(marker.flush(), "")

    def test_an_overlong_body_is_not_a_marker(self):
        text = "<next_thinking>" + "x" * 40 + "</next_thinking>"
        marker = MarkerFilter()
        self.assertEqual(marker.feed(text), text)
        self.assertIsNone(marker.level)
        self.assertEqual(take_marker(text), (text, None))

    def test_an_unknown_level_is_still_taken_out(self):
        """pick_thinking refuses it; the user never sees it either way."""
        marker = MarkerFilter()
        self.assertEqual(marker.feed("<next_thinking>max</next_thinking>ok"), "ok")
        self.assertEqual(marker.level, "max")


class TestClamp(unittest.TestCase):

    def test_supported_levels_pass_through(self):
        for level in ("minimal", "low", "medium", "high"):
            self.assertEqual(clamp_thinking_level(level, FLASH), level)
        self.assertEqual(clamp_thinking_level("medium", PRO), "medium")

    def test_pro_has_no_minimal(self):
        self.assertEqual(clamp_thinking_level("minimal", PRO), "low")
        self.assertEqual(clamp_thinking_level("minimal", f"models/{PRO}"), "low")

    def test_unknown_models_and_none_pass_through(self):
        self.assertEqual(clamp_thinking_level("minimal", "gemini-9-ultra"), "minimal")
        self.assertIsNone(clamp_thinking_level(None, PRO))


class TestExecutionContextField(unittest.TestCase):

    def test_unknown_level_is_rejected(self):
        with self.assertRaises(ValueError):
            _ctx(thinking_level="max")

    def test_with_agent_drops_it_like_model(self):
        ctx = _ctx(thinking_level="low", model="PRO")
        child = ctx.with_agent("goal-setter")
        self.assertIsNone(child.thinking_level)
        self.assertIsNone(child.model)
        self.assertEqual(ctx.with_graph("coding").thinking_level, "low")


class TestModelWrapper(ResolverCase):
    """RetryingChatGoogleGenerativeAI adds the level to every call and records it on a voice trace."""

    def llm(self):
        from core.runtime.model.retry import RetryingChatGoogleGenerativeAI
        return RetryingChatGoogleGenerativeAI(model=FLASH, google_api_key="test")

    def test_resolved_level_is_added(self):
        token = current_execution_context.set(_ctx(surface="voice"))
        try:
            self.assertEqual(self.llm()._with_thinking({})["thinking_level"], "low")
        finally:
            current_execution_context.reset(token)

    def test_explicit_level_wins(self):
        token = current_execution_context.set(_ctx(surface="voice"))
        try:
            self.assertEqual(self.llm()._with_thinking({"thinking_level": "high"})["thinking_level"], "high")
        finally:
            current_execution_context.reset(token)

    def test_nothing_added_without_a_level(self):
        self.assertNotIn("thinking_level", self.llm()._with_thinking({}))

    def test_level_is_recorded_on_the_turn_trace(self):
        from core.runtime.observability.turn_trace import start_turn_trace
        import contextvars

        def run():
            trace = start_turn_trace("day-planner", 1)
            token = current_execution_context.set(_ctx(surface="voice"))
            try:
                self.llm()._with_thinking({})
            finally:
                current_execution_context.reset(token)
            return trace

        trace = contextvars.copy_context().run(run)
        calls = [e for e in trace.events if e["kind"] == "model_call"]
        self.assertEqual(len(calls), 1)
        self.assertEqual((calls[0]["model"], calls[0]["thinking"], calls[0]["agent"]), (FLASH, "low", "day-planner"))


if __name__ == "__main__":
    unittest.main()
