"""graphs/voice: graphs/main, with a voice turn's first model call on a light
model that speaks first and then answers or hands over."""
import io
import json
import unittest
from contextlib import contextmanager, redirect_stdout
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import InMemorySaver

from core.config.models import FLASH, FLASH_LITE
from core.prompts.voice import OPENER_HINT, get_voice_mode_prompt
from core.runtime.foundation.context import current_execution_context
from core.runtime.foundation.sessions import SessionManager
from core.runtime.model.handover import DEFAULT_DEPTH, HANDOVER, MISSING
from core.runtime.model.retry import RetryingChatGoogleGenerativeAI
from core.runtime.model.thinking import PICK_KEY
from core.runtime.observability.turn_trace import start_turn_trace
from graphs.voice.graph import create_graph, first_call, handover, with_light_rules
from graphs.voice.prompts import HandedOverPrompt, LightCallPrompt

PRE_TOOL_LINE = "Before calling tools, write a one-sentence <speech> block"
CONFIG = {"agent_id": "home-test", "channels": ["home-test"], "tools": {}}
HANDED_OVER = HandedOverPrompt().render()


@contextmanager
def _voice_turn(agent_id="home-test"):
    """A voice turn's context, as the room's agent gets it from the router, and its trace."""
    ctx = SessionManager.get_session(agent_id=agent_id, source="tool", channel="home-test", surface="voice")
    token = current_execution_context.set(ctx)
    try:
        yield ctx, start_turn_trace("main", 1)
    finally:
        current_execution_context.reset(token)


def _text(text):
    return ChatGenerationChunk(message=AIMessageChunk(content=text))


def _call(name, args, call_id):
    return ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[
        {"name": name, "args": json.dumps(args), "id": call_id, "index": 0}]))


def _scripted(script, calls):
    """Each model request takes the next list of `script`; `calls` gets what it was sent.
    It streams copies: langchain stamps a streamed chunk with its run's message id,
    so a chunk streamed again would carry an earlier reply's id, and replace it."""
    async def fake(self, messages, stop=None, run_manager=None, **kwargs):
        calls.append({"model": self.model, "thinking": kwargs.get("thinking_level"), "messages": list(messages),
                      "tools": [t["function"]["name"] for t in kwargs.get("tools") or []]})
        for item in script[len(calls) - 1]:
            yield item.model_copy(deep=True)
    return fake


@tool
def get_lights(area: str) -> str:
    """Which lights are on in an area of the house."""
    return "kitchen: on; hallway: off"


def _prompt(state):
    """The agent's prompt as GraphBuilder builds it, cut to the voice block, which comes last."""
    return [SystemMessage(content="You are Butler."), SystemMessage(content=get_voice_mode_prompt()),
            *state["messages"]]


HANDS_OVER = [[_text("<speech>Let me check the "), _text("downstairs lights.</speech><hand"),
               _text('over depth="medium"/>')],
              [_call("get_lights", {"area": "downstairs"}, "g1")],
              [_text("<speech>The kitchen light is on.</speech>Kitchen: on; hallway: off.")]]
ANSWERS = [[_text("<speech>You're welcome!</speech>"), _text("You're welcome!")]]
OPENER_ONLY = [[_text("<speech>Let me check the "), _text("downstairs lights.</speech>")], *HANDS_OVER[1:]]


class TestTheLightCall(unittest.TestCase):

    def test_it_is_the_turns_first_call(self):
        self.assertTrue(first_call({"messages": [AIMessage("Hi."), HumanMessage("Lights?")]}))
        self.assertFalse(first_call({"messages": [HumanMessage("Lights?"), AIMessage("", tool_calls=[
            {"name": HANDOVER, "args": {}, "id": "h1"}])]}))
        self.assertFalse(first_call({"messages": [HumanMessage("Lights?"), ToolMessage("ok", tool_call_id="h1")]}))
        self.assertFalse(first_call({"messages": []}))

    def test_its_rules_go_after_the_system_blocks(self):
        messages = [SystemMessage("agent"), SystemMessage("voice"), HumanMessage("Lights?")]
        out = with_light_rules(messages)
        self.assertEqual([m.content for m in out[:2]], ["agent", "voice"])
        self.assertIn("<light_call>", out[2].content)
        self.assertEqual(out[3:], messages[2:])
        self.assertEqual(len(messages), 3, "the agent's own list is left as it is")

    def test_its_rules(self):
        rules = LightCallPrompt().render()
        self.assertIn('end your response with <handover depth="low"/>', rules)
        self.assertIn("Never a fact, number or status you haven't seen here", rules)
        self.assertIn("When in doubt, hand over", rules)


class TestTheHandover(unittest.IsolatedAsyncioTestCase):

    async def _hand_over(self, depth, **more):
        out = io.StringIO()
        with _voice_turn() as (ctx, trace), redirect_stdout(out):
            result = await handover.ainvoke({"depth": depth, **more})
        return result, ctx, trace, out.getvalue()

    async def test_the_depth_goes_to_the_next_call_and_the_opener_is_noted(self):
        result, ctx, trace, printed = await self._hand_over("medium")
        self.assertEqual(result, HANDED_OVER)
        self.assertEqual(ctx.turn_hints[PICK_KEY], "medium")
        self.assertTrue(ctx.turn_hints[OPENER_HINT])
        self.assertIn("[Handover] agent=home-test depth=medium applied=medium\n", printed)
        (event,) = [e for e in trace.events if e["kind"] == HANDOVER]
        self.assertEqual((event["agent"], event["depth"], event["applied"], event["marker"]),
                         ("home-test", "medium", "medium", "found"))

    async def test_a_missing_marker_is_logged_and_traced(self):
        """The light call said its line and left the marker off: it hands over all the same."""
        result, ctx, trace, printed = await self._hand_over(DEFAULT_DEPTH, marker=MISSING)
        self.assertEqual(result, HANDED_OVER)
        self.assertTrue(ctx.turn_hints[OPENER_HINT])
        self.assertIn(f"[Handover] agent=home-test depth={DEFAULT_DEPTH} applied={DEFAULT_DEPTH} marker=missing\n",
                      printed)
        (event,) = [e for e in trace.events if e["kind"] == HANDOVER]
        self.assertEqual((event["depth"], event["marker"]), (DEFAULT_DEPTH, MISSING))

    async def test_voice_caps_the_depth(self):
        _, ctx, _, printed = await self._hand_over("high")
        self.assertEqual(ctx.turn_hints[PICK_KEY], "medium")
        self.assertIn("depth=high applied=medium", printed)

    async def test_a_depth_that_isnt_one_still_hands_over(self):
        result, ctx, _, _ = await self._hand_over("deep")
        self.assertEqual(result, HANDED_OVER)
        self.assertNotIn(PICK_KEY, ctx.turn_hints, "the agent's usual level")
        self.assertTrue(ctx.turn_hints[OPENER_HINT])

    def test_its_result_tells_the_agents_model_the_line_was_heard(self):
        self.assertIn("the user has heard your first line, so don't say it again", HANDED_OVER)


class TestVoiceTurns(unittest.IsolatedAsyncioTestCase):
    """Whole turns through the compiled graph, on scripted model streams."""

    def _graph(self, checkpointer=None):
        agent = RetryingChatGoogleGenerativeAI(model=FLASH, google_api_key="fake")
        return create_graph(agent, [get_lights], prompt=_prompt, checkpointer=checkpointer, agent_id="home-test",
                            config=CONFIG, light=("FLASH_LITE", "minimal"))

    async def _turn(self, graph, words, script, calls, thread=None):
        """One voice turn: the final state, what streamed, and the trace."""
        streamed = []
        with patch.object(ChatGoogleGenerativeAI, "_astream", _scripted(script, calls)), _voice_turn() as (_, trace):
            async for event in graph.astream_events({"messages": [HumanMessage(words)]}, thread, version="v2"):
                if event["event"] == "on_chat_model_stream" and event["data"]["chunk"].content:
                    streamed.append(event["data"]["chunk"].content)
            state = graph.get_state(thread).values if thread else None
        return state, "".join(streamed), trace

    async def test_a_hand_over(self):
        calls = []
        thread = {"configurable": {"thread_id": "home-test:tool:home-test"}}
        with redirect_stdout(io.StringIO()):
            state, streamed, trace = await self._turn(self._graph(InMemorySaver()), "Any lights on downstairs?",
                                                      HANDS_OVER, calls, thread)

        light, tool_call, answer = calls
        self.assertEqual((light["model"], light["thinking"], light["tools"]), (FLASH_LITE, "minimal", []))
        self.assertEqual((tool_call["model"], tool_call["thinking"], tool_call["tools"]), (FLASH, "medium", ["get_lights"]),
                         "the agent's own model, at the depth asked for, and never with `handover` declared")
        self.assertEqual(answer["thinking"], "low", "then the agent's usual level")
        systems = [[m.content for m in call["messages"] if isinstance(m, SystemMessage)] for call in calls]
        self.assertTrue(any("<light_call>" in s for s in systems[0]))
        self.assertFalse(any("<light_call>" in s for s in systems[1] + systems[2]), "the light call's rules only")
        self.assertIn(PRE_TOOL_LINE, systems[0][1])
        self.assertNotIn(PRE_TOOL_LINE, systems[1][1], "the user heard the opener: not asked to say it again")
        self.assertIn("The user has already heard what you're doing", systems[1][1])

        self.assertEqual(streamed, "<speech>Let me check the downstairs lights.</speech>"
                                   "<speech>The kitchen light is on.</speech>Kitchen: on; hallway: off.")
        kinds = [type(m).__name__ for m in state["messages"]]
        self.assertEqual(kinds, ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage", "ToolMessage", "AIMessage"])
        opener, handed = state["messages"][1:3]
        self.assertEqual(opener.content, "<speech>Let me check the downstairs lights.</speech>")
        self.assertEqual([(c["name"], c["args"]) for c in opener.tool_calls], [(HANDOVER, {"depth": "medium"})])
        self.assertEqual((handed.name, handed.content), (HANDOVER, HANDED_OVER))
        model_calls = [e for e in trace.events if e["kind"] == "model_call"]
        self.assertEqual([e.get("light", False) for e in model_calls], [True, False, False])
        self.assertEqual([(e["depth"], e["marker"]) for e in trace.events if e["kind"] == HANDOVER],
                         [("medium", "found")])

    async def test_an_opener_without_its_marker_still_hands_over(self):
        """The light call said what it would do and left the marker off: the
        agent's model does it, at the voice default, and the log says so."""
        calls, out = [], io.StringIO()
        thread = {"configurable": {"thread_id": "home-test:tool:home-test"}}
        with redirect_stdout(out):
            state, streamed, trace = await self._turn(self._graph(InMemorySaver()), "Any lights on downstairs?",
                                                      OPENER_ONLY, calls, thread)

        self.assertEqual([(call["model"], call["tools"]) for call in calls],
                         [(FLASH_LITE, []), (FLASH, ["get_lights"]), (FLASH, ["get_lights"])])
        self.assertEqual(calls[1]["thinking"], DEFAULT_DEPTH)
        self.assertEqual(streamed, "<speech>Let me check the downstairs lights.</speech>"
                                   "<speech>The kitchen light is on.</speech>Kitchen: on; hallway: off.")
        opener, handed = state["messages"][1:3]
        self.assertEqual([(c["name"], c["args"]) for c in opener.tool_calls],
                         [(HANDOVER, {"depth": DEFAULT_DEPTH, "marker": MISSING})])
        self.assertEqual((handed.name, handed.content), (HANDOVER, HANDED_OVER))
        self.assertEqual([(e["depth"], e["marker"]) for e in trace.events if e["kind"] == HANDOVER],
                         [(DEFAULT_DEPTH, MISSING)])
        self.assertIn("marker=missing", out.getvalue())

    async def test_an_answer_ends_the_turn_on_the_light_call(self):
        calls = []
        thread = {"configurable": {"thread_id": "home-test:tool:home-test"}}
        state, streamed, trace = await self._turn(self._graph(InMemorySaver()), "Thanks!", ANSWERS, calls, thread)

        self.assertEqual([call["model"] for call in calls], [FLASH_LITE])
        self.assertEqual(streamed, "<speech>You're welcome!</speech>You're welcome!")
        self.assertEqual([m.content for m in state["messages"]], ["Thanks!", "<speech>You're welcome!</speech>"
                                                                            "You're welcome!"])
        self.assertFalse([e for e in trace.events if e["kind"] == HANDOVER])

    async def test_the_next_turn_starts_on_the_light_model_again(self):
        """On a history holding the last turn's hand-over: the light call reads it, with no tools."""
        graph, thread = self._graph(InMemorySaver()), {"configurable": {"thread_id": "home-test:tool:home-test"}}
        with redirect_stdout(io.StringIO()):
            await self._turn(graph, "Any lights on downstairs?", HANDS_OVER, [], thread)
        calls = []
        await self._turn(graph, "Thanks!", ANSWERS, calls, thread)

        (light,) = calls
        self.assertEqual((light["model"], light["tools"]), (FLASH_LITE, []))
        read = [m for m in light["messages"] if not isinstance(m, SystemMessage)]
        self.assertEqual([type(m).__name__ for m in read],
                         ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage", "ToolMessage", "AIMessage",
                          "HumanMessage"])

    async def test_a_second_hand_over_goes_after_the_first(self):
        """Most voice turns hand over: the next one's opener, hand-over and answer
        follow the last turn's, which stay as they were."""
        graph, thread = self._graph(InMemorySaver()), {"configurable": {"thread_id": "home-test:tool:home-test"}}
        calls = []
        with redirect_stdout(io.StringIO()):
            first, _, _ = await self._turn(graph, "Any lights on downstairs?", HANDS_OVER, [], thread)
            state, streamed, trace = await self._turn(graph, "And upstairs?", HANDS_OVER, calls, thread)

        self.assertEqual([(call["model"], call["tools"]) for call in calls],
                         [(FLASH_LITE, []), (FLASH, ["get_lights"]), (FLASH, ["get_lights"])])
        messages = state["messages"]
        self.assertEqual(messages[:6], first["messages"], "the first turn's messages, unchanged")
        self.assertEqual([type(m).__name__ for m in messages[6:]],
                         ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage", "ToolMessage", "AIMessage"])
        self.assertEqual([c["name"] for c in messages[7].tool_calls], [HANDOVER])
        self.assertEqual((messages[8].name, messages[8].content), (HANDOVER, HANDED_OVER))
        self.assertEqual(streamed, "<speech>Let me check the downstairs lights.</speech>"
                                   "<speech>The kitchen light is on.</speech>Kitchen: on; hallway: off.")
        self.assertEqual([e["depth"] for e in trace.events if e["kind"] == HANDOVER], ["medium"])

    def test_without_a_light_model_it_is_graphs_mains_own(self):
        llm = MagicMock()
        with patch("graphs.voice.graph.create_main_graph") as main:
            create_graph(llm, [get_lights], agent_id="home-test", config=CONFIG)
        main.assert_called_once_with(llm, [get_lights], prompt=None, checkpointer=None, agent_id="home-test",
                                     config=CONFIG)


if __name__ == "__main__":
    unittest.main()
