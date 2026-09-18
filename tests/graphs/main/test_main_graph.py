import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

# Add root directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.runtime.delegation import DelegationResult
from graphs.main.graph import create_graph, format_output, prepare_input

ORCHESTRATOR = {
    "agent_id": "main",
    "tools": {"agent_call": {}},
    # Read from config now, so the fixture has to declare them to exercise the
    # escape hatch at all.
    "concierge_prefixes": ["[main]", "[concierge]", "jobs", "kill"],
}


class TestMainGraph(unittest.TestCase):

    def test_prepare_input_without_caller(self):
        result = prepare_input("Hello, how are you?")
        self.assertIn("messages", result)
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0]["content"], "Hello, how are you?")

    def test_prepare_input_with_caller(self):
        result = prepare_input("Hello", caller="day-planner")
        self.assertIn("messages", result)
        self.assertEqual(result["messages"][0]["content"], "<caller>day-planner</caller>\nHello")

    def test_prepare_input_does_not_duplicate_caller(self):
        result = prepare_input("<caller>existing</caller>\nHello", caller="day-planner")
        self.assertEqual(result["messages"][0]["content"], "<caller>existing</caller>\nHello")

    def test_format_output_with_messages(self):
        state = {
            "messages": [
                HumanMessage(content="Hi"),
                AIMessage(content="Hello there!")
            ]
        }
        self.assertEqual(format_output(state), "Hello there!")

    def test_format_output_fallback(self):
        state = {"raw_key": "raw_val"}
        self.assertEqual(format_output(state), str(state))

    def test_create_graph_returns_compiled_graph(self):
        mock_llm = MagicMock()
        graph = create_graph(llm=mock_llm, tools=[])
        self.assertIsNotNone(graph)
        self.assertTrue(hasattr(graph, "ainvoke"))


class TestMainGraphRouting(unittest.IsolatedAsyncioTestCase):
    """Exercises the compiled graph, not just the decision function."""

    def _loader(self, hosts=("receipts",), eligible=("receipt-processor",)):
        loader = MagicMock()
        loader.hosts_channel.side_effect = lambda agent_id, channel: channel in hosts
        loader.eligible_agents_for_channel.return_value = list(eligible)
        loader.get_agent_config.return_value = {"emoji": "🧾", "name": "Receipt Processor"}
        return loader

    def _concierge(self, reply="concierge reply"):
        """Stands in for the compiled ReAct agent."""
        agent = MagicMock()

        async def ainvoke(state, config=None):
            return {"messages": list(state["messages"]) + [AIMessage(content=reply)]}

        agent.ainvoke = ainvoke
        return agent

    async def _run(self, prompt, loader, delegate_result=None, concierge_reply="concierge reply"):
        """Builds the graph with doubles and runs one turn inside a context."""
        from tests.helpers import execution_context

        delegate = AsyncMock(
            return_value=delegate_result
            or DelegationResult(agent_id="receipt-processor", text="Logged $12.40.")
        )

        with patch("graphs.main.graph.create_react_agent", return_value=self._concierge(concierge_reply)), \
             patch("graphs.main.graph.stream_delegate", delegate), \
             patch("core.loaders.agents_loader.AgentsLoader", return_value=loader), \
             patch("graphs.main.graph._record_turn") as record:
            graph = create_graph(llm=MagicMock(), tools=[], agent_id="main", config=ORCHESTRATOR)
            with execution_context(agent_id="main", source="discord", channel="receipts", stateless=False):
                state = await graph.ainvoke({"messages": [HumanMessage(content=prompt)]})

        return state, delegate, record

    async def test_sole_eligible_agent_is_called_without_a_model(self):
        state, delegate, _ = await self._run("scan this receipt", self._loader())

        delegate.assert_awaited_once()
        kwargs = delegate.await_args.kwargs
        self.assertEqual(kwargs["agent_id"], "receipt-processor")
        self.assertEqual(kwargs["prompt"], "scan this receipt")
        self.assertEqual(kwargs["channel"], "receipts")
        self.assertEqual(kwargs["caller"], "main")

    async def test_delegated_reply_is_attributed_in_history(self):
        """Unattributed, a later concierge turn would read the specialist's words
        as its own and defend them as its own reasoning."""
        state, _, _ = await self._run("scan this receipt", self._loader())

        self.assertEqual(state["messages"][-1].content, "🧾 Receipt Processor: Logged $12.40.")

    async def test_ambiguous_channel_reaches_the_concierge(self):
        loader = self._loader(
            hosts=("weekend-planning",), eligible=("day-planner", "excursion-planner")
        )
        loader.hosts_channel.side_effect = lambda agent_id, channel: True

        state, delegate, _ = await self._run("plan my weekend", loader)

        delegate.assert_not_awaited()
        self.assertEqual(state["messages"][-1].content, "concierge reply")

    async def test_escape_prefix_reaches_the_concierge_without_the_prefix(self):
        loader = self._loader()
        captured = {}

        from tests.helpers import execution_context

        concierge = MagicMock()

        async def ainvoke(state, config=None):
            captured["messages"] = list(state["messages"])
            return {"messages": list(state["messages"]) + [AIMessage(content="ok")]}

        concierge.ainvoke = ainvoke

        with patch("graphs.main.graph.create_react_agent", return_value=concierge), \
             patch("graphs.main.graph.stream_delegate", AsyncMock()) as delegate, \
             patch("core.loaders.agents_loader.AgentsLoader", return_value=loader):
            graph = create_graph(llm=MagicMock(), tools=[], agent_id="main", config=ORCHESTRATOR)
            with execution_context(agent_id="main", source="discord", channel="receipts", stateless=False):
                await graph.ainvoke({"messages": [HumanMessage(content="[main] what did we decide?")]})

        delegate.assert_not_awaited()
        self.assertEqual(captured["messages"][-1].content, "what did we decide?")

    async def test_routed_turn_is_recorded_for_later_fallback(self):
        """The session log is written by LLM callbacks that never fire on this
        path, so the node has to record the turn itself."""
        _, _, record = await self._run("scan this receipt", self._loader())

        record.assert_called_once()
        _session, user_prompt, reply = record.call_args.args
        self.assertEqual(user_prompt, "scan this receipt")
        self.assertEqual(reply, "🧾 Receipt Processor: Logged $12.40.")

    async def test_failed_delegation_is_reported_in_the_channel(self):
        result = DelegationResult(
            agent_id="receipt-processor",
            error="Agent 'receipt-processor' cannot be called in channel 'receipts'.",
        )
        state, _, _ = await self._run("scan this", self._loader(), delegate_result=result)

        self.assertIn("Could not reach `receipt-processor`", state["messages"][-1].content)

    async def test_specialist_agents_never_route(self):
        """Every agent shares this graph. A specialist must behave exactly as it
        did before the router existed."""
        from tests.helpers import execution_context

        loader = self._loader(hosts=(), eligible=())

        with patch("graphs.main.graph.create_react_agent", return_value=self._concierge("specialist reply")), \
             patch("graphs.main.graph.stream_delegate", AsyncMock()) as delegate, \
             patch("core.loaders.agents_loader.AgentsLoader", return_value=loader):
            graph = create_graph(
                llm=MagicMock(),
                tools=[],
                agent_id="receipt-processor",
                config={"agent_id": "receipt-processor", "tools": {"filesystem": {}}},
            )
            with execution_context(
                agent_id="receipt-processor", source="discord", channel="receipts", stateless=False
            ):
                state = await graph.ainvoke({"messages": [HumanMessage(content="scan this")]})

        delegate.assert_not_awaited()
        self.assertEqual(state["messages"][-1].content, "specialist reply")


if __name__ == "__main__":
    unittest.main()
