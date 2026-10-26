import unittest
from unittest.mock import MagicMock

from core.runtime.streaming import StreamHandler


class TestStreamHandler(unittest.IsolatedAsyncioTestCase):
    async def test_stream_graph_events_chat_and_tools(self):
        mock_graph = MagicMock()

        async def mock_astream_events(*args, **kwargs):
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": MagicMock(content="Hello ")}
            }
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": MagicMock(content="world")}
            }
            yield {
                "event": "on_tool_start",
                "name": "calc",
                "data": {"input": {"x": 1}},
                "run_id": "run_1"
            }
            yield {
                "event": "on_tool_end",
                "name": "calc",
                "data": {"output": "2"},
                "run_id": "run_1"
            }

        mock_graph.astream_events = mock_astream_events
        events = []
        async for ev in StreamHandler.stream_graph_events(mock_graph, {}, {}):
            events.append(ev)

        self.assertEqual(len(events), 4)
        self.assertEqual(events[0], {"type": "token", "content": "Hello "})
        self.assertEqual(events[1], {"type": "token", "content": "world"})
        self.assertEqual(events[2], {"type": "tool_start", "tool_name": "calc", "tool_args": {"x": 1}, "run_id": "run_1"})
        self.assertEqual(events[3], {"type": "tool_end", "tool_name": "calc", "output": "2", "run_id": "run_1"})

    async def test_stream_with_recovery_on_corrupt_checkpoint(self):
        mock_graph = MagicMock()
        attempts = 0

        async def mock_astream_events(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise Exception("tool_calls that do not have a corresponding ToolMessage")
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": MagicMock(content="Recovered")}
            }

        mock_graph.astream_events = mock_astream_events
        recovered_sessions = []

        def recover_fn(session):
            recovered_sessions.append(session)

        def is_corrupt_fn(e):
            return "tool_calls that do not have a corresponding ToolMessage" in str(e)

        session = MagicMock()
        events = []
        async for ev in StreamHandler.stream_with_recovery(
            mock_graph, {}, {}, session, recover_fn, is_corrupt_fn
        ):
            events.append(ev)

        self.assertEqual(attempts, 2)
        self.assertEqual(len(recovered_sessions), 1)
        self.assertEqual(events, [{"type": "token", "content": "Recovered"}])

    async def test_stream_graph_events_suppresses_post_subagent_chat_tokens(self):
        from core.runtime.foundation.events import SUBAGENT_STREAM_FINAL, SUBAGENT_STREAM_TOKEN
        mock_graph = MagicMock()

        async def mock_astream_events(*args, **kwargs):
            # 1. Subagent emits header and content tokens
            yield {
                "event": "on_custom_event",
                "name": SUBAGENT_STREAM_TOKEN,
                "data": {"content": "🔬 Ted: ", "agent_id": "topic-researcher", "is_header": True}
            }
            yield {
                "event": "on_custom_event",
                "name": SUBAGENT_STREAM_TOKEN,
                "data": {"content": "Research findings complete.", "agent_id": "topic-researcher"}
            }
            yield {
                "event": "on_custom_event",
                "name": SUBAGENT_STREAM_FINAL,
                "data": {"agent_id": "topic-researcher", "text": "Research findings complete."}
            }
            # 2. Main orchestrator LLM generates turn 2 closing tokens
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": MagicMock(content="Here is what Ted found: Research findings complete.")}
            }

        mock_graph.astream_events = mock_astream_events
        events = []
        async for ev in StreamHandler.stream_graph_events(mock_graph, {}, {}):
            events.append(ev)

        token_contents = [ev["content"] for ev in events if ev["type"] == "token"]
        self.assertEqual(token_contents, ["🔬 Ted: ", "Research findings complete."])
        # Main's post-subagent chat model tokens must be suppressed to avoid repeating text
        self.assertNotIn("Here is what Ted found", "".join(token_contents))


class TestOnlyTheOwningAgentsTokensAreStreamed(unittest.IsolatedAsyncioTestCase):
    """`astream_events` reports descendants too, and they are not the answer.

    A graph node that reaches a stateless worker through `agent_call` runs
    inside this callback tree, so the worker's model tokens arrive here looking
    exactly like the orchestrator's own. Streaming them is what posted raw
    `<worker_handoff>` XML into `#software-dev` — once per implement attempt.
    The existing `has_subagent_streamed` guard never fired for it, because a
    stateless agent returns its result instead of dispatching custom events.
    """

    def _graph_with(self, events):
        graph = MagicMock()

        async def astream_events(*args, **kwargs):
            for event in events:
                yield event

        graph.astream_events = astream_events
        return graph

    @staticmethod
    def _chat(content, agent_id=None):
        event = {"event": "on_chat_model_stream", "data": {"chunk": MagicMock(content=content)}}
        if agent_id:
            event["metadata"] = {"agent_id": agent_id}
        return event

    async def test_a_nested_workers_tokens_never_reach_the_channel(self):
        graph = self._graph_with([
            self._chat("Running the coding graph now.", agent_id="main"),
            self._chat("<worker_handoff><status>READY_FOR_TEST</status>", agent_id="graph-worker"),
            self._chat("</worker_handoff>", agent_id="graph-worker"),
        ])

        events = [ev async for ev in
                  StreamHandler.stream_graph_events(graph, {}, {}, agent_id="main")]

        self.assertEqual([ev["content"] for ev in events], ["Running the coding graph now."])

    async def test_the_owning_agents_own_tokens_still_stream(self):
        graph = self._graph_with([self._chat("Hello ", agent_id="main"),
                                  self._chat("world", agent_id="main")])

        events = [ev async for ev in
                  StreamHandler.stream_graph_events(graph, {}, {}, agent_id="main")]

        self.assertEqual([ev["content"] for ev in events], ["Hello ", "world"])

    async def test_tokens_with_no_owner_recorded_are_not_dropped(self):
        """Absent metadata means "unknown", not "someone else".

        Dropping those would silence any run whose config does not carry an
        agent id — the whole turn would arrive as nothing.
        """
        graph = self._graph_with([self._chat("anonymous token")])

        events = [ev async for ev in
                  StreamHandler.stream_graph_events(graph, {}, {}, agent_id="main")]

        self.assertEqual([ev["content"] for ev in events], ["anonymous token"])

    async def test_subagent_chat_token_skipped_before_custom_event(self):
        """When a subagent emits on_chat_model_stream before agent_call dispatches
        SUBAGENT_STREAM_TOKEN, the subagent's chat stream token must be skipped so that
        the stream does not repeat the first words (e.g. 'Let\'🌼 Daisy: Let\'s pick up')."""
        from core.runtime.foundation.events import SUBAGENT_STREAM_TOKEN

        events = [
            # 1. Subagent LLM emits its first chunk
            self._chat("Let'", agent_id="day-planner"),
            # 2. agent_call dispatches header
            {
                "event": "on_custom_event",
                "name": SUBAGENT_STREAM_TOKEN,
                "data": {"content": "🌼 Daisy: ", "agent_id": "day-planner", "is_header": True}
            },
            # 3. agent_call dispatches the first token
            {
                "event": "on_custom_event",
                "name": SUBAGENT_STREAM_TOKEN,
                "data": {"content": "Let'", "agent_id": "day-planner"}
            },
            # 4. agent_call dispatches subsequent tokens
            {
                "event": "on_custom_event",
                "name": SUBAGENT_STREAM_TOKEN,
                "data": {"content": "s pick up", "agent_id": "day-planner"}
            }
        ]
        graph = self._graph_with(events)
        streamed = [ev async for ev in StreamHandler.stream_graph_events(graph, {}, {}, agent_id="main")]
        tokens = [ev["content"] for ev in streamed if ev["type"] == "token"]
        # Must start with the header, NOT the duplicated "Let'"
        self.assertEqual(tokens, ["🌼 Daisy: ", "Let'", "s pick up"])
        self.assertEqual("".join(tokens), "🌼 Daisy: Let's pick up")

    async def test_subagent_token_flags_are_forwarded(self):
        """The consumer learns which tokens are an attribution or a callee's
        error: the channel shows both, and voice must not read them out."""
        from core.runtime.foundation.events import SUBAGENT_STREAM_TOKEN

        def token(**data):
            return {"event": "on_custom_event", "name": SUBAGENT_STREAM_TOKEN, "data": data}

        graph = self._graph_with([
            token(content="🛎️ Butler: ", agent_id="home-steward", is_header=True),
            token(content="Checking", agent_id="home-steward"),
            token(content="\n[Error: 503]", agent_id="home-steward", is_error=True),
        ])
        streamed = [ev async for ev in StreamHandler.stream_graph_events(graph, {}, {}, agent_id="main")]
        self.assertEqual(
            [(ev["content"], ev["is_header"], ev["is_error"]) for ev in streamed if ev["type"] == "token"],
            [("🛎️ Butler: ", True, False), ("Checking", False, False), ("\n[Error: 503]", False, True)],
        )

    async def test_agent_id_inferred_from_config(self):
        """If agent_id argument is None, it is inferred from config metadata."""
        graph = self._graph_with([
            self._chat("worker token", agent_id="subagent"),
            self._chat("orchestrator token", agent_id="main")
        ])
        config = {"metadata": {"agent_id": "main"}}
        streamed = [ev async for ev in StreamHandler.stream_graph_events(graph, {}, config, agent_id=None)]
        tokens = [ev["content"] for ev in streamed if ev["type"] == "token"]
        self.assertEqual(tokens, ["orchestrator token"])


class TestANodesOwnText(unittest.IsolatedAsyncioTestCase):
    """graphs/main's fixed line has no model behind it. The voice speaks only
    what streams, so the node dispatches its text, and it must arrive as the
    owner's token -- in the owner's voice, and suppressing nothing."""

    async def test_it_streams_as_a_token_from_its_agent(self):
        from core.runtime.foundation.events import NODE_STREAM_TOKEN

        graph = MagicMock()

        async def astream_events(*args, **kwargs):
            yield {"event": "on_custom_event", "name": NODE_STREAM_TOKEN,
                   "data": {"content": "<speech>Who's it for?</speech>", "agent_id": "main"}}
            yield {"event": "on_custom_event", "name": NODE_STREAM_TOKEN, "data": {"content": ""}}
            yield {"event": "on_chat_model_stream", "data": {"chunk": MagicMock(content="later")}}

        graph.astream_events = astream_events
        streamed = [ev async for ev in StreamHandler.stream_graph_events(graph, {}, {})]

        self.assertEqual(streamed, [
            {"type": "token", "content": "<speech>Who's it for?</speech>", "agent_id": "main"},
            {"type": "token", "content": "later"},
        ])


if __name__ == "__main__":
    unittest.main()
