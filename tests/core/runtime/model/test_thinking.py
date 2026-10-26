"""Which thinking level each model call runs at (core.runtime.model.thinking)."""
import unittest
from unittest.mock import MagicMock, patch

from core.runtime.foundation.context import current_execution_context
from core.runtime.foundation.sessions import SessionManager
from core.runtime.model.thinking import resolve_thinking_level
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
