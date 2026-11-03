import asyncio
import io
import os
import sys
import time
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

# Add root directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.channel.response_parser import AgentResponse
from core.runtime.delegation import DelegationResult
from core.runtime.foundation import events
from core.runtime.foundation.context import current_execution_context
from core.runtime.foundation.sessions import SessionManager
from core.runtime.history.handoff import SWITCH_CAPSULE_TOKENS
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
    """What the router and the capsule builder ask of AgentsLoader, from TEAM (or `team`)."""

    def __init__(self, team=None):
        self.team = team or TEAM

    def list_agent_ids(self):
        return list(self.team)

    def get_agent_config(self, agent_id):
        return self.team.get(agent_id)

    def hosts_channel(self, agent_id, channel):
        return channel in (self.team.get(agent_id) or {}).get("channel_hosts", [])

    def eligible_agents_for_channel(self, channel, exclude_agent_id=None):
        return sorted(a for a, c in self.team.items()
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


# TEAM, and two agents that hand you to each other: Aki answers #agent-management,
# Elephant #software-dev. The graph worker is reachable everywhere and owns no channel.
SWITCH_TEAM = {
    **TEAM,
    "main": {**TEAM["main"], "channel_hosts": TEAM["main"]["channel_hosts"] + ["agent-management", "software-dev"]},
    "agent-designer": {"name": "Aki", "emoji": "🧬", "channels": ["agent-management", "general"]},
    "software-planner": {"name": "Elephant", "emoji": "🐘", "channels": ["software-dev", "general"]},
    "graph-worker": {"name": "Graph Worker", "channels": ["*"], "stateless": True},
}
CAPSULE = '<handoff_context from="agent-designer" channel="agent-management" via="switch">plan</handoff_context>'
SWITCHES = 'Handing you to Elephant. <switch to="software-planner">Spec the inbox scanner.</switch>'


class _SwitchTurns(unittest.IsolatedAsyncioTestCase):
    """Turns through the compiled graph with SWITCH_TEAM, the capsule store
    real (on conftest's throwaway memory.db)."""

    async def _turn(self, prompt, channel, reply="Noted.", capsule=CAPSULE, delegate=None):
        """One turn in `channel`, whose agent replies `reply`. Returns the
        stream_delegate and capsule_for doubles, and what the turn printed."""
        response = AgentResponse.from_string(reply)
        delegate = delegate or AsyncMock(return_value=DelegationResult(
            agent_id="any", text=response.text, response=response))
        patches = [
            patch("graphs.main.graph.create_react_agent", return_value=TestMainGraphRouting._concierge(self)),
            patch("graphs.main.graph.stream_delegate", delegate),
            patch("graphs.main.graph.safe_dispatch_custom_event", new_callable=AsyncMock),
            patch("core.loaders.agents_loader.AgentsLoader", return_value=_TeamLoader(SWITCH_TEAM)),
            patch("graphs.main.graph._record_turn"),
        ]
        if capsule is not None:
            patches.append(patch("graphs.main.graph.capsule_for", return_value=capsule))
        started = [p.start() for p in patches]
        printed = io.StringIO()
        try:
            graph = create_graph(llm=MagicMock(), tools=[], agent_id="main", config=SWITCH_TEAM["main"])
            with _session(channel=channel), redirect_stdout(printed):
                state = await graph.ainvoke({"messages": [HumanMessage(content=prompt)]})
        finally:
            for p in reversed(patches):
                p.stop()
        self.state = state
        return delegate, (started[-1] if capsule is not None else None), printed.getvalue()

    def _waiting(self):
        from core.storage.handoff_capsules import HandoffCapsuleStore

        return HandoffCapsuleStore().query("SELECT * FROM handoff_capsules WHERE consumed_at IS NULL "
                                           "AND superseded_at IS NULL")


class TestSwitchCapsules(_SwitchTurns):
    """A reply ending with <switch to="..."> parks a capsule of the conversation
    for the teammate (core/storage/handoff_capsules.py), and the teammate's next
    turn in its own channel gets it, once."""

    async def test_a_reply_that_switches_you_parks_a_capsule_for_the_teammate(self):
        _, capsule_for, printed = await self._turn("I think you can hand this over now.", "agent-management",
                                                   reply=SWITCHES)

        capsule_for.assert_called_once_with("agent-management", loader=ANY, brief="Spec the inbox scanner.",
                                            via="switch", max_tokens=SWITCH_CAPSULE_TOKENS)
        (row,) = self._waiting()
        self.assertEqual((row["from_agent"], row["from_channel"], row["to_agent"], row["to_channel"], row["via"],
                          row["brief"], row["capsule"]),
                         ("agent-designer", "agent-management", "software-planner", "software-dev", "switch",
                          "Spec the inbox scanner.", CAPSULE))
        self.assertIn(f"[graphs.main] Parked hand-off capsule #{row['id']} for 'software-planner' in "
                      f"#software-dev: {len(CAPSULE)} chars, with a brief", printed)
        # The reply itself is as it was: attributed, without the tag or the brief.
        self.assertEqual(self.state["messages"][-1].content, "🧬 Aki: Handing you to Elephant.")

    async def test_the_teammates_next_turn_in_its_channel_gets_it_once(self):
        await self._turn("I think you can hand this over now.", "agent-management", reply=SWITCHES)
        (row,) = self._waiting()

        delegate, _, printed = await self._turn("What did Aki tell you?", "software-dev")
        self.assertEqual(delegate.await_args.kwargs["prompt"], f"What did Aki tell you?\n\n{CAPSULE}")
        self.assertEqual(delegate.await_args.kwargs["agent_id"], "software-planner")
        self.assertIn(f"[graphs.main] Attached parked capsule #{row['id']} from 'agent-designer' "
                      f"({len(CAPSULE)} chars)", printed)
        from core.storage.handoff_capsules import HandoffCapsuleStore
        self.assertEqual(HandoffCapsuleStore().get(row["id"]).consumed_session, "software-planner:tool:software-dev")

        delegate, _, _ = await self._turn("And then?", "software-dev")
        self.assertEqual(delegate.await_args.kwargs["prompt"], "And then?")

    async def test_end_to_end_the_teammate_gets_the_conversation_and_the_brief(self):
        """Real capsule builder and session store. The switching reply is in
        the transcript once the run returns, as LoggingHandler leaves it
        (see TestTheReplyIsLoggedBeforeTheRunEnds)."""
        from core.storage.sessions import SessionStore

        aki = "agent-designer:tool:agent-management"
        store = SessionStore()
        store.append_message(aki, "user", "<caller>main</caller>\nLet's replace the inbox prompt with a script.")
        store.append_message(aki, "ai", "<speech>Agreed.</speech>Plan: a script schedule with a state file.")

        async def aki_answers(**kwargs):
            store.append_message(aki, "user", f"<caller>main</caller>\n{kwargs['prompt']}")
            store.append_message(aki, "ai", SWITCHES)
            response = AgentResponse.from_string(SWITCHES)
            return DelegationResult(agent_id=kwargs["agent_id"], text=response.text, response=response)

        await self._turn("I think you can hand this over now.", "agent-management", capsule=None,
                         delegate=AsyncMock(side_effect=aki_answers))
        delegate, _, _ = await self._turn("What did Aki tell you?", "software-dev", capsule=None)

        prompt = delegate.await_args.kwargs["prompt"]
        self.assertTrue(prompt.startswith('What did Aki tell you?\n\n<handoff_context from="agent-designer" '
                                          'channel="agent-management" captured="'))
        self.assertIn('via="switch">\n<brief from="Aki">\nSpec the inbox scanner.\n</brief>\n<recent>', prompt)
        for line in ("You: Let's replace the inbox prompt with a script.",
                     "Aki: Plan: a script schedule with a state file.",
                     "You: I think you can hand this over now.",
                     "Aki: Handing you to Elephant."):
            self.assertIn(line, prompt)
        self.assertEqual(prompt.count("<handoff_context"), 1)
        self.assertNotIn("<switch", prompt)

    async def test_an_expired_capsule_is_not_attached(self):
        from core.storage.handoff_capsules import TTL_S, HandoffCapsuleStore

        HandoffCapsuleStore().park(from_agent="agent-designer", from_channel="agent-management",
                                   to_agent="software-planner", to_channel="software-dev", capsule=CAPSULE,
                                   now=time.time() - TTL_S - 1)
        delegate, _, _ = await self._turn("What did Aki tell you?", "software-dev")
        self.assertEqual(delegate.await_args.kwargs["prompt"], "What did Aki tell you?")

    async def test_nothing_is_parked_for_no_channel_for_itself_or_for_this_channel(self):
        for teammate in ("graph-worker", "nobody", "agent-designer", "Aki"):
            with self.subTest(teammate=teammate):
                _, capsule_for, _ = await self._turn("Over to them.", "agent-management",
                                                     reply=f'Sure. <switch to="{teammate}">brief</switch>')
                capsule_for.assert_not_called()
        with patch("graphs.main.graph.home_channel", return_value="agent-management"):
            _, capsule_for, _ = await self._turn("Over to them.", "agent-management", reply=SWITCHES)
        capsule_for.assert_not_called()
        self.assertEqual(self._waiting(), [])

    async def test_a_capsule_that_cant_be_parked_never_breaks_the_turn(self):
        with patch("core.storage.handoff_capsules.HandoffCapsuleStore.park", side_effect=RuntimeError("disk full")), \
             redirect_stderr(io.StringIO()) as errors:
            await self._turn("I think you can hand this over now.", "agent-management", reply=SWITCHES)
        self.assertEqual(self.state["messages"][-1].content, "🧬 Aki: Handing you to Elephant.")
        self.assertIn("could not park a hand-off capsule for 'software-planner': disk full", errors.getvalue())

    async def test_a_capsule_that_cant_be_read_never_breaks_the_turn(self):
        with patch("core.storage.handoff_capsules.HandoffCapsuleStore.take", side_effect=RuntimeError("locked")), \
             redirect_stderr(io.StringIO()) as errors:
            delegate, _, _ = await self._turn("What did Aki tell you?", "software-dev")
        self.assertEqual(delegate.await_args.kwargs["prompt"], "What did Aki tell you?")
        self.assertIn("could not read parked hand-off capsules for 'software-planner': locked", errors.getvalue())


class TestSwitchCapsulesAndHandOffs(_SwitchTurns):
    """The router's hand-off (you name the agent) with capsules parked by a <switch>."""

    def _park(self, from_channel="agent-management", brief="Spec the inbox scanner."):
        from core.storage.handoff_capsules import HandoffCapsuleStore

        return HandoffCapsuleStore().park(from_agent="agent-designer", from_channel=from_channel,
                                          to_agent="software-planner", to_channel="software-dev",
                                          capsule="<parked/>", brief=brief)

    async def test_a_plain_hand_off_is_as_it_was(self):
        delegate, capsule_for, _ = await self._turn("Elephant, spec this one", "general",
                                                    capsule="<handoff_context>general</handoff_context>")
        capsule_for.assert_called_once_with("general", loader=ANY, brief=None, via="handoff")
        self.assertEqual((delegate.await_args.kwargs["agent_id"], delegate.await_args.kwargs["channel"]),
                         ("software-planner", "software-dev"))
        self.assertEqual(delegate.await_args.kwargs["prompt"],
                         "Elephant, spec this one\n\n<handoff_context>general</handoff_context>")
        self.assertEqual(self._waiting(), [])

    async def test_one_parked_from_another_channel_goes_after_the_hand_offs_own(self):
        self._park()
        delegate, _, _ = await self._turn("Elephant, go ahead", "general",
                                          capsule="<handoff_context>general</handoff_context>")
        self.assertEqual(delegate.await_args.kwargs["prompt"],
                         "Elephant, go ahead\n\n<handoff_context>general</handoff_context>\n\n<parked/>")
        self.assertEqual(self._waiting(), [], "taken: it isn't attached again in #software-dev")

    async def test_one_parked_from_the_same_channel_is_replaced_by_the_newer_capsule_with_its_brief(self):
        self._park()
        delegate, capsule_for, printed = await self._turn("Elephant, go ahead", "agent-management",
                                                          capsule="<handoff_context>newer</handoff_context>")
        capsule_for.assert_called_once_with("agent-management", loader=ANY, brief="Spec the inbox scanner.",
                                            via="handoff")
        self.assertEqual(delegate.await_args.kwargs["prompt"],
                         "Elephant, go ahead\n\n<handoff_context>newer</handoff_context>")
        self.assertIn("is the conversation this hand-off came from", printed)
        self.assertEqual(self._waiting(), [])

    async def test_a_hand_offs_reply_that_switches_you_on_parks_a_capsule_from_its_channel(self):
        _, capsule_for, _ = await self._turn("Ted, find the festival dates", "pkm-wiki",
                                             reply='Found them. <switch to="day-planner">Block Oct 17-18.</switch>')
        capsule_for.assert_called_with("topic-research", loader=ANY, brief="Block Oct 17-18.", via="switch",
                                       max_tokens=SWITCH_CAPSULE_TOKENS)
        (row,) = self._waiting()
        self.assertEqual((row["from_agent"], row["from_channel"], row["to_agent"], row["to_channel"]),
                         ("topic-researcher", "topic-research", "day-planner", "day-planning"))


class TestTheReplyIsLoggedBeforeTheRunEnds(unittest.IsolatedAsyncioTestCase):
    """Why a switch's capsule is built as soon as stream_delegate returns, not
    later: the reply that switched is already in the transcript. LoggingHandler
    writes it in on_llm_end, which LangChain awaits before the model call
    returns -- so before the graph's event stream ends, and so before
    Agent.execute_stream yields its final response."""

    async def test_the_reply_is_in_the_transcript_when_the_stream_ends(self):
        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
        from langgraph.prebuilt import create_react_agent

        from core.runtime.observability.transcript import LoggingHandler
        from core.storage.sessions import SessionStore

        session = SessionManager.get_session("agent-designer", source="tool", channel="agent-management")
        graph = create_react_agent(GenericFakeChatModel(messages=iter([AIMessage(content=SWITCHES)])), [])
        config = {"callbacks": [LoggingHandler(session=session, human_message="hand it over")],
                  "metadata": {"agent_id": "agent-designer"}}
        async for _ in graph.astream_events({"messages": [HumanMessage("hand it over")]}, config=config,
                                            version="v2"):
            pass

        rows = SessionStore().recent_history(session.session_id, limit=10)
        self.assertEqual([(r["from"], r["message"]) for r in rows], [("user", "hand it over"), ("ai", SWITCHES)])


if __name__ == "__main__":
    unittest.main()
