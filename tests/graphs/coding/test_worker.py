"""The graph binding that a worker's permissions depend on.

`graph-worker`'s own `agent.json` grants it nothing: every tool and path it can
reach comes from the graph it is bound to. These tests pin that the binding is
established on both entry paths, because when it was missing on the scheduled
one the worker could not even `ls` its own worktree — and the tick recorded that
as a model failure and spent the whole implement budget on it.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from graphs.coding.utils.worker import (
    TICK_CALLER_ID,
    WORKER_AGENT_ID,
    _bind_graph,
    call_worker,
    worker_session,
)


class WorkerTestCase(unittest.IsolatedAsyncioTestCase):
    """Collaborators are imported inside the functions, so they are patched
    where they are defined."""

    def setUp(self):
        self.session = patch("core.runtime.session_manager.SessionManager.get_session")
        self.mock_get_session = self.session.start()
        self.mock_get_session.side_effect = lambda **kw: MagicMock(**{
            "graph_id": kw.get("graph_id"), "agent_id": kw.get("agent_id")
        })
        self.addCleanup(self.session.stop)

    def ambient(self, graph_id):
        """Installs an ambient context bound to `graph_id`, as `graph_call` does."""
        from core.runtime.execution_context import current_execution_context

        ctx = MagicMock()
        ctx.graph_id = graph_id
        ctx.with_graph.side_effect = lambda g: MagicMock(graph_id=g)
        token = current_execution_context.set(ctx)
        self.addCleanup(current_execution_context.reset, token)
        return ctx


class TestWorkerSession(WorkerTestCase):
    def test_the_session_carries_the_graph_it_must_inherit_grants_from(self):
        worker_session("coding")

        kwargs = self.mock_get_session.call_args.kwargs
        self.assertEqual(kwargs["graph_id"], "coding")
        self.assertEqual(kwargs["agent_id"], WORKER_AGENT_ID)

    def test_the_worker_is_stateless(self):
        """It is given its whole task in the prompt; a carried-over history
        would let one task's context leak into the next."""
        worker_session("coding")
        self.assertTrue(self.mock_get_session.call_args.kwargs["stateless"])


class TestBindGraph(WorkerTestCase):
    def test_an_unbound_tick_gets_a_context_of_its_own(self):
        """The scheduled path: `coding_tick.py` invokes the graph directly, so
        there is no caller to inherit from and the graph must speak for itself."""
        token = _bind_graph("coding")

        self.assertIsNotNone(token)
        self.assertEqual(
            self.mock_get_session.call_args.kwargs["agent_id"], TICK_CALLER_ID
        )
        self.assertEqual(self.mock_get_session.call_args.kwargs["graph_id"], "coding")

    def test_a_caller_in_another_graph_is_rebound_not_replaced(self):
        """Rebinding keeps the caller's channel and session identity, which is
        what keeps interactive output going back where it came from."""
        ctx = self.ambient("main")

        token = _bind_graph("coding")

        self.assertIsNotNone(token)
        ctx.with_graph.assert_called_once_with("coding")
        self.mock_get_session.assert_not_called()

    def test_an_already_bound_caller_is_left_alone(self):
        """The interactive path: `graph_call` has already bound the context, and
        re-creating it would throw away the channel it is streaming to."""
        self.ambient("coding")

        self.assertIsNone(_bind_graph("coding"))
        self.mock_get_session.assert_not_called()


class TestCallWorker(WorkerTestCase):
    def setUp(self):
        super().setUp()
        # `agent_call` is a pydantic StructuredTool and refuses attribute
        # assignment, so the tool itself is replaced rather than its method.
        self.tool = patch("tools.agent_call.agent_call")
        self.mock_tool = self.tool.start()
        self.mock_invoke = AsyncMock(return_value="<worker_handoff/>")
        self.mock_tool.ainvoke = self.mock_invoke
        self.addCleanup(self.tool.stop)

    async def test_the_worker_runs_under_the_graphs_grants(self):
        from core.runtime.execution_context import current_execution_context

        seen = {}

        async def _capture(_payload):
            ctx = current_execution_context.get()
            seen["graph_id"] = ctx.graph_id if ctx else None
            return "ok"

        self.mock_invoke.side_effect = _capture
        await call_worker("build it", graph_id="coding", channel="c")

        self.assertEqual(seen["graph_id"], "coding")

    async def test_the_binding_is_undone_afterwards(self):
        """A leaked binding would follow whatever ran next on this task."""
        from core.runtime.execution_context import current_execution_context

        await call_worker("build it", graph_id="coding", channel="c")

        self.assertIsNone(current_execution_context.get())

    async def test_the_binding_is_undone_even_when_the_worker_raises(self):
        self.mock_invoke.side_effect = RuntimeError("boom")
        from core.runtime.execution_context import current_execution_context

        with self.assertRaises(RuntimeError):
            await call_worker("build it", graph_id="coding", channel="c")

        self.assertIsNone(current_execution_context.get())

    async def test_it_returns_the_workers_reply_verbatim(self):
        """`implement` parses the handoff out of this string."""
        self.mock_invoke.return_value = "<worker_handoff><status>OK</status></worker_handoff>"

        reply = await call_worker("build it", graph_id="coding", channel="c")

        self.assertIn("<status>OK</status>", reply)

    async def test_a_missing_graph_id_still_binds_the_coding_graph(self):
        """An unbound worker is the incident; defaulting is safer than silently
        handing it an empty roster."""
        await call_worker("build it", graph_id="", channel="c")

        self.assertEqual(self.mock_get_session.call_args.kwargs["graph_id"], "coding")


if __name__ == "__main__":
    unittest.main()
