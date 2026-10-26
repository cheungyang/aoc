"""The turn hooks (core/runtime/hooks.py): how they're wired, and that a failing
one can't break a turn that already ran."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.runtime import bootstrap, hooks
from core.runtime.agent import Agent
from core.runtime.foundation.sessions import SessionManager


class TestWiring(unittest.TestCase):

    def tearDown(self):
        bootstrap.wire()

    def test_wire_registers_the_three_in_order(self):
        """The prune is waited for before the job is registered, and the job is
        settled before the memory log is written."""
        bootstrap.wire()
        self.assertEqual([type(h) for h in hooks.turn_hooks()],
                         [hooks.PruneHook, hooks.JobHook, hooks.MemoryLogHook])

    def test_an_unwired_runtime_says_what_to_call(self):
        hooks.set_turn_hooks(None)
        with self.assertRaisesRegex(RuntimeError, r"bootstrap\.wire\(\)"):
            hooks.turn_hooks()


class TestAHookThatFails(unittest.IsolatedAsyncioTestCase):

    def tearDown(self):
        bootstrap.wire()

    async def test_after_and_ended_failures_are_logged_not_raised(self):
        class Broken(hooks.TurnHook):
            async def after(self, turn):
                raise RuntimeError("after broke")

            def ended(self, turn):
                raise RuntimeError("ended broke")

        seen = MagicMock()

        class Watching(hooks.TurnHook):
            async def after(self, turn):
                seen(turn.response.text)

        hooks.set_turn_hooks([Broken(), Watching()])
        graph = MagicMock()
        graph.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="Reply")]})
        agent = Agent("test-agent", {})
        agent._graphs[""] = graph
        session = SessionManager.get_session(agent_id="test-agent", source="job")

        with patch("core.runtime.agent.LoggingHandler"), patch("sys.stderr") as err:
            self.assertEqual(await agent.execute("hello", session=session), "Reply")

        seen.assert_called_once_with("Reply")  # the next hook still ran
        logged = "".join(str(c.args[0]) for c in err.write.call_args_list)
        self.assertIn("Broken.after failed: after broke", logged)
        self.assertIn("Broken.ended failed: ended broke", logged)


class TestMemoryLogHook(unittest.IsolatedAsyncioTestCase):

    @patch("core.runtime.hooks.save_agent_memory_log")
    async def test_a_reply_from_a_delegated_agent_is_not_logged_as_this_ones(self, save):
        """Agent leaves `turn.response` empty when a subagent's reply came back."""
        session = SessionManager.get_session(agent_id="main", source="job")
        await hooks.MemoryLogHook().after(hooks.Turn("main", {}, session, "hi", response=None))
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
