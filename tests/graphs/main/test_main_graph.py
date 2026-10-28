import asyncio
import os
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

# Add root directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.channel.response_parser import AgentResponse
from core.runtime.delegation import DelegationResult
from core.runtime.foundation import events
from core.runtime.foundation.context import current_execution_context
from core.runtime.foundation.sessions import SessionManager
from graphs.main.graph import (
    UNADDRESSED_SPEECH,
    UNADDRESSED_TEXT,
    create_graph,
    fixed_reply,
    format_output,
    prepare_input,
)

# The Concierge's shape: reachable everywhere, owning no channel, holding no tools.
ORCHESTRATOR = {"agent_id": "main", "channels": ["*"], "tools": {}}


@contextmanager
def _session(agent_id="main", source="discord", channel="receipts", surface=None):
    """Runs the block inside the context a turn of that shape would get."""
    ctx = SessionManager.get_session(agent_id=agent_id, source=source, channel=channel, surface=surface)
    token = current_execution_context.set(ctx)
    try:
        yield ctx
    finally:
        current_execution_context.reset(token)


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


class TestFixedReply(unittest.TestCase):
    """What a message gets where no single agent answers, as each surface needs it."""

    def test_typed_it_offers_a_name_the_agents_channels_and_the_commands(self):
        self.assertEqual(fixed_reply("text"), UNADDRESSED_TEXT)
        self.assertIn('"Daisy, …"', UNADDRESSED_TEXT)  # starting with a name hands the message off
        self.assertIn("!help", UNADDRESSED_TEXT)

    def test_spoken_it_is_all_speech(self):
        """The voice speaks only <speech>. With no other text, the channel shows
        the speech as the reply (DiscordStreamBuffer.finalize), and no
        missing-speech alert fires."""
        response = AgentResponse.from_string(fixed_reply("voice"))
        self.assertEqual(response.speech, UNADDRESSED_SPEECH)
        self.assertEqual(response.text, "")

    def test_spoken_it_gives_a_whole_example(self):
        """Spoken, "…" is only a pause, so the example is a whole sentence."""
        self.assertIn("'Daisy, what's on today?'", UNADDRESSED_SPEECH)
        self.assertNotIn("…", UNADDRESSED_SPEECH)


class TestMainGraphRouting(unittest.IsolatedAsyncioTestCase):
    """Exercises the compiled graph, not just the decision function."""

    def _loader(self, hosts=("receipts",), eligible=("receipt-processor",)):
        loader = MagicMock()
        loader.hosts_channel.side_effect = lambda agent_id, channel: channel in hosts
        loader.eligible_agents_for_channel.return_value = list(eligible)
        loader.get_agent_config.return_value = {"emoji": "🧾", "name": "Receipt Processor"}
        return loader

    def _concierge(self, reply="concierge reply"):
        """Stands in for the compiled ReAct agent, and counts its turns."""
        agent = MagicMock()
        agent.calls = 0

        async def ainvoke(state, config=None):
            agent.calls += 1
            return {"messages": list(state["messages"]) + [AIMessage(content=reply)]}

        agent.ainvoke = ainvoke
        return agent

    async def _run(self, prompt, loader, delegate_result=None, concierge_reply="concierge reply",
                   agent_id="main", config=ORCHESTRATOR, source="discord", channel="receipts", surface=None):
        """Builds the graph with doubles and runs one turn inside a context."""
        delegate = AsyncMock(
            return_value=delegate_result
            or DelegationResult(agent_id="receipt-processor", text="Logged $12.40.")
        )
        concierge = self._concierge(concierge_reply)

        with patch("graphs.main.graph.create_react_agent", return_value=concierge), \
             patch("graphs.main.graph.stream_delegate", delegate), \
             patch("graphs.main.graph.safe_dispatch_custom_event", new_callable=AsyncMock) as dispatch, \
             patch("core.loaders.agents_loader.AgentsLoader", return_value=loader), \
             patch("graphs.main.graph._record_turn") as record:
            graph = create_graph(llm=MagicMock(), tools=[], agent_id=agent_id, config=config)
            with _session(agent_id=agent_id, source=source, channel=channel, surface=surface):
                state = await graph.ainvoke({"messages": [HumanMessage(content=prompt)]})

        return state, delegate, record, dispatch, concierge

    def _shared(self, channel="general"):
        return self._loader(hosts=(channel,), eligible=("day-planner", "meal-planner"))

    async def test_sole_eligible_agent_is_called_without_a_model(self):
        state, delegate, _, _, concierge = await self._run("scan this receipt", self._loader())

        delegate.assert_awaited_once()
        kwargs = delegate.await_args.kwargs
        self.assertEqual(kwargs["agent_id"], "receipt-processor")
        self.assertEqual(kwargs["prompt"], "scan this receipt")
        self.assertEqual(kwargs["channel"], "receipts")
        self.assertEqual(kwargs["caller"], "main")
        self.assertIs(kwargs["routed"], True)  # the agent you're talking to: it keeps follow_up
        self.assertEqual(concierge.calls, 0)

    async def test_delegated_reply_is_attributed_in_history(self):
        """Unattributed, a later concierge turn would read the specialist's words
        as its own and defend them as its own reasoning."""
        state, _, _, _, _ = await self._run("scan this receipt", self._loader())

        self.assertEqual(state["messages"][-1].content, "🧾 Receipt Processor: Logged $12.40.")

    async def test_a_shared_channel_gets_the_fixed_line_without_a_model(self):
        state, delegate, record, dispatch, concierge = await self._run(
            "what's for dinner?", self._shared(), channel="general"
        )

        delegate.assert_not_awaited()
        self.assertEqual(concierge.calls, 0)
        self.assertEqual(state["messages"][-1].content, UNADDRESSED_TEXT)
        # Streamed, so a surface that reads only the stream gets it too.
        dispatch.assert_awaited_once_with(
            events.NODE_STREAM_TOKEN, {"content": UNADDRESSED_TEXT, "agent_id": "main"}
        )
        # And logged, since no model callback will log it.
        record.assert_called_once()
        _session_ctx, user_prompt, reply = record.call_args.args
        self.assertEqual((user_prompt, reply), ("what's for dinner?", UNADDRESSED_TEXT))

    async def test_the_voice_lobby_hears_the_spoken_line(self):
        state, _, _, dispatch, concierge = await self._run(
            "what's for dinner?", self._shared(), channel="general", surface="voice"
        )

        self.assertEqual(concierge.calls, 0)
        spoken = f"<speech>{UNADDRESSED_SPEECH}</speech>"
        self.assertEqual(state["messages"][-1].content, spoken)
        self.assertEqual(dispatch.await_args.args[1]["content"], spoken)

    async def test_a_former_concierge_prefix_is_delegated_like_any_message(self):
        """`[main]` was routing syntax; now it is just the user's words."""
        state, delegate, _, _, concierge = await self._run("[main] what did we decide?", self._loader())

        delegate.assert_awaited_once()
        self.assertEqual(delegate.await_args.kwargs["prompt"], "[main] what did we decide?")
        self.assertEqual(concierge.calls, 0)

    async def test_a_turn_addressed_to_the_concierge_reaches_its_model(self):
        """The dream standup delegates to the Concierge in #general. Routing that
        would answer the dream with the fixed line."""
        state, delegate, _, dispatch, concierge = await self._run(
            "<dream prompt>", self._shared(), source="tool", channel="general", concierge_reply="<dream_response/>"
        )

        delegate.assert_not_awaited()
        dispatch.assert_not_awaited()
        self.assertEqual(concierge.calls, 1)
        self.assertEqual(state["messages"][-1].content, "<dream_response/>")

    async def test_routed_turn_is_recorded_for_later_fallback(self):
        """The session log is written by LLM callbacks that never fire on this
        path, so the node has to record the turn itself."""
        _, _, record, _, _ = await self._run("scan this receipt", self._loader())

        record.assert_called_once()
        _session_ctx, user_prompt, reply = record.call_args.args
        self.assertEqual(user_prompt, "scan this receipt")
        self.assertEqual(reply, "🧾 Receipt Processor: Logged $12.40.")

    async def test_failed_delegation_is_reported_in_the_channel(self):
        result = DelegationResult(
            agent_id="receipt-processor",
            error="Agent 'receipt-processor' cannot be called in channel 'receipts'.",
        )
        state, _, _, _, _ = await self._run("scan this", self._loader(), delegate_result=result)

        self.assertIn("Could not reach `receipt-processor`", state["messages"][-1].content)

    async def test_specialist_agents_never_route(self):
        """Every agent shares this graph. A specialist must behave exactly as it
        did before the router existed."""
        state, delegate, _, dispatch, _ = await self._run(
            "scan this",
            self._loader(hosts=(), eligible=()),
            concierge_reply="specialist reply",
            agent_id="receipt-processor",
            config={"agent_id": "receipt-processor", "channels": ["receipts", "general"], "tools": {"filesystem": {}}},
            source="tool",
        )

        delegate.assert_not_awaited()
        dispatch.assert_not_awaited()
        self.assertEqual(state["messages"][-1].content, "specialist reply")

    async def test_an_agent_hosting_its_own_channel_answers_it_itself(self):
        """Were a specialist to run its own bot and host its channel, its
        channel's messages must reach its model, not the fixed line."""
        state, delegate, _, dispatch, _ = await self._run(
            "design the auth flow",
            self._loader(hosts=("software-dev",), eligible=()),
            concierge_reply="elephant reply",
            agent_id="software-planner",
            config={"agent_id": "software-planner", "channels": ["general", "software-dev"], "tools": {}},
            channel="software-dev",
        )

        delegate.assert_not_awaited()
        dispatch.assert_not_awaited()
        self.assertEqual(state["messages"][-1].content, "elephant reply")


# A small team, as configured: the Concierge hosts every channel; each
# specialist answers its own and lists #general.
TEAM = {
    "main": {"agent_id": "main", "name": "Concierge", "channels": ["*"], "tools": {},
             "channel_hosts": ["general", "pkm-wiki", "topic-research", "day-planning"]},
    "wiki-gardener": {"name": "William", "emoji": "🌿", "channels": ["pkm-wiki", "general"]},
    "topic-researcher": {"name": "Ted", "emoji": "🔬", "channels": ["topic-research", "general"]},
    "day-planner": {"name": "Daisy", "emoji": "🌼", "channels": ["day-planning", "general"]},
}


class _TeamLoader:
    """What the router and the capsule builder ask of AgentsLoader, from TEAM."""

    def list_agent_ids(self):
        return list(TEAM)

    def get_agent_config(self, agent_id):
        return TEAM.get(agent_id)

    def hosts_channel(self, agent_id, channel):
        return channel in (TEAM.get(agent_id) or {}).get("channel_hosts", [])

    def eligible_agents_for_channel(self, channel, exclude_agent_id=None):
        return sorted(a for a, c in TEAM.items()
                      if a != exclude_agent_id and "*" not in c["channels"] and channel in c["channels"])


class TestHandoffNode(unittest.IsolatedAsyncioTestCase):
    """A message that names another agent goes to that agent's own channel."""

    async def _run(self, prompt, channel="pkm-wiki", surface=None, capsule="<handoff_context/>",
                   delegate_result=None, record=True):
        delegate = AsyncMock(return_value=delegate_result or DelegationResult(
            agent_id="topic-researcher", text="Starting a syllabus on prompt caching."))
        concierge = TestMainGraphRouting._concierge(self)
        patches = [
            patch("graphs.main.graph.create_react_agent", return_value=concierge),
            patch("graphs.main.graph.stream_delegate", delegate),
            patch("graphs.main.graph.safe_dispatch_custom_event", new_callable=AsyncMock),
            patch("core.loaders.agents_loader.AgentsLoader", return_value=_TeamLoader()),
        ]
        if capsule is not None:
            patches.append(patch("graphs.main.graph.capsule_for", return_value=capsule))
        if record:
            patches.append(patch("graphs.main.graph._record_turn"))
        started = [p.start() for p in patches]
        try:
            dispatch = started[2]
            recorder = started[-1] if record else None
            graph = create_graph(llm=MagicMock(), tools=[], agent_id="main", config=TEAM["main"])
            with _session(channel=channel, surface=surface):
                state = await graph.ainvoke({"messages": [HumanMessage(content=prompt)]})
        finally:
            for p in reversed(patches):
                p.stop()
        return state, delegate, dispatch, recorder, concierge

    async def test_the_named_agent_answers_in_its_own_channel_with_the_capsule(self):
        state, delegate, _, _, concierge = await self._run("Ted create a syllabus on THIS topic")

        kwargs = delegate.await_args.kwargs
        self.assertEqual((kwargs["agent_id"], kwargs["channel"], kwargs["caller"]),
                         ("topic-researcher", "topic-research", "main"))
        self.assertEqual(kwargs["prompt"], "Ted create a syllabus on THIS topic\n\n<handoff_context/>")
        self.assertIs(kwargs["routed"], True)  # you named it: it keeps follow_up
        self.assertEqual(concierge.calls, 0)

    async def test_the_readers_are_told_before_the_reply_streams(self):
        _, _, dispatch, _, _ = await self._run(
            'Ted, take THIS\n\n<replying_to author="William">Ingested an article</replying_to>')

        dispatch.assert_awaited_once_with(events.ROUTE_HANDOFF, {
            "agent_id": "topic-researcher",
            "name": "Ted",
            "emoji": "🔬",
            "channel": "topic-research",
            "pointer": "→ 🔬 Ted has this in #topic-research",
            "request": "Ted, take THIS",
        })

    async def test_this_turn_keeps_only_the_message_and_the_pointer(self):
        """The checkpoint and the host's log of #pkm-wiki: never Ted's reply."""
        state, _, _, recorder, _ = await self._run("Ted, take this one")

        pointer = "→ 🔬 Ted has this in #topic-research"
        self.assertEqual([m.content for m in state["messages"]], ["Ted, take this one", pointer])
        _ctx, user_prompt, reply = recorder.call_args.args
        self.assertEqual((user_prompt, reply), ("Ted, take this one", pointer))

    async def test_naming_an_agent_in_general_hands_off(self):
        _, delegate, _, _, _ = await self._run("Daisy, I'm on a trip to Tahoe with my family", channel="general")
        self.assertEqual((delegate.await_args.kwargs["agent_id"], delegate.await_args.kwargs["channel"]),
                         ("day-planner", "day-planning"))

    async def test_a_spoken_hand_off_takes_the_same_path(self):
        _, delegate, dispatch, _, _ = await self._run("hey ted, take this one", surface="voice")
        self.assertEqual(delegate.await_args.kwargs["agent_id"], "topic-researcher")
        self.assertEqual(dispatch.await_args.args[0], events.ROUTE_HANDOFF)

    async def test_without_a_capsule_the_message_goes_as_typed(self):
        _, delegate, _, _, _ = await self._run("Ted, take this one", capsule="")
        self.assertEqual(delegate.await_args.kwargs["prompt"], "Ted, take this one")

    async def test_a_failed_hand_off_is_reported(self):
        failed = DelegationResult(agent_id="topic-researcher", error="Unknown agent 'topic-researcher'")
        state, _, _, recorder, _ = await self._run("Ted, take this one", delegate_result=failed)
        self.assertIn("Could not hand this to `topic-researcher`", state["messages"][-1].content)
        recorder.assert_not_called()


class TestHandoffLeavesTheSourceUntouched(unittest.IsolatedAsyncioTestCase):
    """The checkpoint comparison: a hand-off out of #pkm-wiki reads William's
    session for the capsule, and William's session is byte-for-byte what it
    was. Real session store and checkpointer, on conftest's throwaway memory.db."""

    def _snapshot(self, session):
        from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
        from core.storage.sessions import SessionStore

        found = SqliteCheckpointer().get_tuple(
            {"configurable": {"thread_id": session.get_session_thread_id(), "checkpoint_ns": ""}})
        return (SessionStore().load_history(session.session_id, limit=0), found.checkpoint["id"],
                [(m.type, m.content) for m in found.checkpoint["channel_values"]["messages"]])

    async def test_the_source_session_is_untouched(self):
        from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
        from core.runtime.history.channel_posts import append_to_session
        from core.storage.sessions import SessionStore

        william = SessionManager.get_session("wiki-gardener", source="tool", channel="pkm-wiki")
        store = SessionStore()
        store.append_message(william.session_id, "user", "<caller>main</caller>\nhow does it relate to memory?")
        store.append_message(william.session_id, "ai", "Close cousins: compaction versus prompt caching.")
        for message in (HumanMessage("how does it relate to memory?"),
                        AIMessage("Close cousins: compaction versus prompt caching.")):
            append_to_session(william, message, checkpointer=SqliteCheckpointer())
        before = self._snapshot(william)

        state, delegate, _, _, _ = await TestHandoffNode._run(self, "Ted create a syllabus on THIS topic",
                                                              capsule=None, record=False)

        self.assertEqual(self._snapshot(william), before)
        prompt = delegate.await_args.kwargs["prompt"]
        self.assertTrue(prompt.startswith("Ted create a syllabus on THIS topic\n\n<handoff_context "))
        self.assertIn("William: Close cousins: compaction versus prompt caching.", prompt)
        # The host's own log of #pkm-wiki gets the message and the pointer, no more.
        host_log = store.load_history(SessionManager.get_session("main", source="discord", channel="pkm-wiki")
                                      .session_id, limit=0)
        self.assertEqual([(r["from"], r["message"]) for r in host_log],
                         [("user", "Ted create a syllabus on THIS topic"),
                          ("ai", "→ 🔬 Ted has this in #topic-research")])


if __name__ == "__main__":
    unittest.main()
