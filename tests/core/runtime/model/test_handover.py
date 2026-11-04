"""The light first call's hand-over marker (core/runtime/model/handover.py): taken
out of the reply as it streams, and turned into a `handover` tool call."""
import contextvars
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_google_genai import ChatGoogleGenerativeAI

from core.config.models import FLASH, FLASH_LITE
from core.runtime.model.handover import DEFAULT_DEPTH, HANDOVER, HandoverFilter, LightChatModel, take_handover
from core.runtime.model.retry import RetryingChatGoogleGenerativeAI
from core.runtime.observability.turn_trace import start_turn_trace


def _streamed(*texts):
    async def fake(self, messages, stop=None, run_manager=None, **kwargs):
        for text in texts:
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))
    return fake


def _light():
    return LightChatModel(model=FLASH_LITE, google_api_key="fake", max_retries=1)


class TestTheMarker(unittest.TestCase):

    def test_a_whole_reply(self):
        self.assertEqual(take_handover('<speech>Let me check.</speech><handover depth="low"/>'),
                         ("<speech>Let me check.</speech>", "low"))
        self.assertEqual(take_handover("<speech>It's 4 pm.</speech>It's 4 pm."), ("<speech>It's 4 pm.</speech>It's 4 pm.",
                                                                                  None))

    def test_the_ways_a_model_writes_it(self):
        for marker, depth in [('<handover depth="medium"/>', "medium"), ("<handover depth='minimal' />", "minimal"),
                              ("<handover depth=low>", "low"), ('<HANDOVER DEPTH="Medium"/>', "medium"),
                              ("<handover/>", DEFAULT_DEPTH), ("<handover>", DEFAULT_DEPTH)]:
            with self.subTest(marker=marker):
                self.assertEqual(take_handover(f"<speech>On it.</speech>{marker}"), ("<speech>On it.</speech>", depth))

    def test_split_across_chunks(self):
        marker = HandoverFilter()
        out = [marker.feed(t) for t in ("<speech>Let me check.</speech><hand", 'over dep', 'th="low"/', ">\n")]
        self.assertEqual("".join(out), "<speech>Let me check.</speech>")
        self.assertEqual((marker.found, marker.depth), (True, "low"))

    def test_other_tags_go_straight_out(self):
        marker = HandoverFilter()
        self.assertEqual(marker.feed("<speech>Three handovers.</speech> <handovers> 2 < 3"),
                         "<speech>Three handovers.</speech> <handovers> 2 < 3")
        self.assertEqual((marker.found, marker.depth), (False, None))

    def test_a_start_still_held_at_the_end_is_flushed(self):
        marker = HandoverFilter()
        self.assertEqual(marker.feed("Done <handov"), "Done ")
        self.assertEqual(marker.flush(), "<handov")
        self.assertFalse(marker.found)


class TestLightChatModel(unittest.IsolatedAsyncioTestCase):

    async def _stream(self, *texts):
        with patch.object(ChatGoogleGenerativeAI, "_astream", _streamed(*texts)):
            chunks = [chunk async for chunk in _light().astream([HumanMessage("Any lights on downstairs?")])]
        reply = chunks[0]
        for chunk in chunks[1:]:
            reply += chunk
        return chunks, reply

    async def test_the_marker_becomes_a_handover_call(self):
        chunks, reply = await self._stream("<speech>Let me check the ", "lights.</speech><handover ",
                                           'depth="medium"/>')
        self.assertEqual("".join(c.content for c in chunks if isinstance(c.content, str)),
                         "<speech>Let me check the lights.</speech>", "never heard nor shown")
        (call,) = reply.tool_calls
        self.assertEqual((call["name"], call["args"]), (HANDOVER, {"depth": "medium"}))
        self.assertTrue(call["id"].startswith("handover-"))

    async def test_an_answer_has_no_call(self):
        _, reply = await self._stream("<speech>It's 4 pm.</speech>", "It's 4 pm.")
        self.assertEqual((reply.content, reply.tool_calls), ("<speech>It's 4 pm.</speech>It's 4 pm.", []))

    async def test_a_marker_without_a_depth_asks_for_the_voice_default(self):
        _, reply = await self._stream("<speech>On it.</speech><hand", "over/>")
        self.assertEqual([c["args"] for c in reply.tool_calls], [{"depth": DEFAULT_DEPTH}])

    async def test_a_reply_not_streamed(self):
        async def generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[ChatGeneration(
                message=AIMessage(content='<speech>Checking.</speech><handover depth="low"/>'))])

        with patch.object(ChatGoogleGenerativeAI, "_agenerate", generate):
            result = await _light()._agenerate([HumanMessage("Are the lights on?")])
        message = result.generations[0].message
        self.assertEqual(message.content, "<speech>Checking.</speech>")
        self.assertEqual([(c["name"], c["args"]) for c in message.tool_calls], [(HANDOVER, {"depth": "low"})])

    def test_its_calls_say_light_on_the_voice_trace(self):
        def calls(llm):
            trace = start_turn_trace("main", 1)
            llm._with_thinking({"thinking_level": "minimal"})
            return [e for e in trace.events if e["kind"] == "model_call"]

        (light,) = contextvars.copy_context().run(calls, _light())
        (agent,) = contextvars.copy_context().run(
            calls, RetryingChatGoogleGenerativeAI(model=FLASH, google_api_key="fake"))
        self.assertEqual((light["model"], light["thinking"], light.get("light")), (FLASH_LITE, "minimal", True))
        self.assertNotIn("light", agent)


if __name__ == "__main__":
    unittest.main()
