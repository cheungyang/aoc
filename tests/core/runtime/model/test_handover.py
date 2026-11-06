"""The light first call's model (core/runtime/model/handover.py): only the reply's
first <speech> block, then a `handover` call; one attempt and a deadline, and
LightCallFailed when it has nothing for the user to hear."""
import asyncio
import contextlib
import contextvars
import io
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_google_genai import ChatGoogleGenerativeAI

from core.config.models import FLASH, FLASH_LITE
from core.runtime.model import handover as handover_module
from core.runtime.model.handover import (DEADLINE, ERROR, HANDOVER, NO_SPEECH, REFUSED, LightCallFailed,
                                         LightChatModel, OpenerFilter)
from core.runtime.model.retry import RetryingChatGoogleGenerativeAI
from core.runtime.observability.turn_trace import start_turn_trace

OVERLOADED = "503 UNAVAILABLE: The model is overloaded. Please try again later."


def _light():
    return LightChatModel(model=FLASH_LITE, google_api_key="fake")


def _chunk(text, **usage):
    return ChatGenerationChunk(message=AIMessageChunk(content=text, usage_metadata=usage or None))


class TestOpenerFilter(unittest.TestCase):

    def _feed(self, *texts):
        opener = OpenerFilter()
        return "".join(opener.feed(text) for text in texts), opener

    def test_only_the_first_speech_block(self):
        out, opener = self._feed("<speech>Let me check.</speech>The kitchen light is on.")
        self.assertEqual(out, "<speech>Let me check.</speech>")
        self.assertTrue(opener.said and opener.done)

    def test_tags_split_across_chunks(self):
        out, opener = self._feed("<spe", "ech>Let me ", "check.</sp", "eech>The kitchen light is on.")
        self.assertEqual(out, "<speech>Let me check.</speech>")
        self.assertTrue(opener.done)

    def test_text_before_the_block_is_dropped(self):
        self.assertEqual(self._feed("Sure. <speech>On it.</speech>")[0], "<speech>On it.</speech>")

    def test_the_tags_case(self):
        self.assertEqual(self._feed("<SPEECH>On it.</Speech>")[0], "<speech>On it.</speech>")

    def test_no_block_or_an_empty_one_says_nothing(self):
        for texts in (["It's 4 pm."], ["<speech>", "  ", "</speech>It's 4 pm."], [""]):
            with self.subTest(texts=texts):
                out, opener = self._feed(*texts)
                self.assertEqual(out, "")
                self.assertFalse(opener.said)
                self.assertEqual(opener.close(), "")

    def test_words_wait_for_the_block_to_have_some(self):
        opener = OpenerFilter()
        self.assertEqual([opener.feed(t) for t in ("<speech>", " ", "On it.")], ["", "", "<speech>On it."])

    def test_a_block_cut_short_is_closed(self):
        out, opener = self._feed("<speech>Let me ch", "eck the li</spe")
        self.assertEqual(out, "<speech>Let me check the li")
        self.assertEqual(opener.close(), "</speech>")
        self.assertEqual(opener.close(), "", "closed once")

    def test_nothing_after_the_block(self):
        opener = OpenerFilter()
        opener.feed("<speech>On it.</speech>")
        self.assertEqual(opener.feed("<speech>Again.</speech>"), "")


class TestLightChatModel(unittest.IsolatedAsyncioTestCase):

    async def _stream(self, fake):
        """Streams the light model's reply over `fake` (the SDK stream): its chunks
        and their sum, the error it raised, and the voice trace's events."""
        chunks, error = [], None

        async def run():
            trace = start_turn_trace("main", 1)
            nonlocal error
            try:
                with patch.object(ChatGoogleGenerativeAI, "_astream", fake):
                    async for chunk in _light().astream([HumanMessage("Any lights on downstairs?")]):
                        chunks.append(chunk)
            except LightCallFailed as e:
                error = e
            return trace

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            trace = await asyncio.create_task(run(), context=contextvars.copy_context())
        reply = None
        for chunk in chunks:
            reply = chunk if reply is None else reply + chunk
        return chunks, reply, error, trace.events, out.getvalue()

    @staticmethod
    def _streamed(*items, pulled=None):
        """A fake SDK stream: its items in turn, an exception raised where one is."""
        async def fake(self, messages, stop=None, run_manager=None, **kwargs):
            for item in items:
                if isinstance(item, BaseException):
                    raise item
                if pulled is not None:
                    pulled.append(item)
                yield item if isinstance(item, ChatGenerationChunk) else _chunk(item)
        return fake

    async def test_says_only_its_first_line_then_hands_over(self):
        chunks, reply, error, events, _ = await self._stream(self._streamed(
            "<speech>Let me check the ", "downstairs lights.</speech>", "The kitchen light is on."))
        self.assertIsNone(error)
        self.assertEqual("".join(c.content for c in chunks), "<speech>Let me check the downstairs lights.</speech>")
        (call,) = reply.tool_calls
        self.assertEqual((call["name"], call["args"]), (HANDOVER, {}))
        self.assertTrue(call["id"].startswith("handover-"))
        self.assertFalse([e for e in events if e["kind"] == "light_fallback"])

    async def test_an_answer_is_cut_to_its_first_line(self):
        """It never answers: whatever follows its first line is dropped, and the turn hands over."""
        _, reply, _, _, _ = await self._stream(self._streamed("<speech>It's 4 pm.</speech>It's 4 pm. Anything else?"))
        self.assertEqual((reply.content, [c["name"] for c in reply.tool_calls]), ("<speech>It's 4 pm.</speech>",
                                                                                 [HANDOVER]))

    async def test_it_stops_reading_once_its_line_is_said(self):
        pulled = []
        await self._stream(self._streamed("<speech>On it.</speech>", "More words.", "And more.", pulled=pulled))
        self.assertEqual(pulled, ["<speech>On it.</speech>"])

    async def test_a_line_that_breaks_off_is_closed_and_handed_over(self):
        chunks, reply, error, events, printed = await self._stream(self._streamed(
            "<speech>Let me ch", RuntimeError("connection reset")))
        self.assertIsNone(error, "the user heard its words: the agent's model goes on from them")
        self.assertEqual(reply.content, "<speech>Let me ch</speech>")
        self.assertEqual([c["name"] for c in reply.tool_calls], [HANDOVER])
        self.assertEqual([e["reason"] for e in events if e["kind"] == "light_cut"], [ERROR])
        self.assertIn("[LightCall] broke off after its first words", printed)

    async def test_the_usage_of_its_first_chunks_is_kept(self):
        _, reply, _, _, _ = await self._stream(self._streamed(
            _chunk("", input_tokens=900, output_tokens=0, total_tokens=900),
            _chunk("<speech>On it.</speech>", input_tokens=0, output_tokens=4, total_tokens=4)))
        self.assertEqual((reply.usage_metadata["input_tokens"], reply.usage_metadata["output_tokens"]), (900, 4))

    async def test_nothing_to_say_falls_back(self):
        for items in (["It's 4 pm."], ["<speech></speech>It's 4 pm."], []):
            with self.subTest(items=items):
                chunks, _, error, events, printed = await self._stream(self._streamed(*items))
                self.assertIsInstance(error, LightCallFailed)
                self.assertEqual(chunks, [], "nothing reached the user")
                (event,) = [e for e in events if e["kind"] == "light_fallback"]
                self.assertEqual(event["reason"], NO_SPEECH)
                self.assertIn("[LightCall] fell back to the agent's model: no_speech", printed)

    async def test_refused_it_falls_back_on_the_first_attempt(self):
        attempts = []

        async def refused(self, messages, stop=None, run_manager=None, **kwargs):
            attempts.append(1)
            raise Exception(OVERLOADED)
            yield  # an async generator

        with patch("core.runtime.model.retry._async_sleep") as sleep:
            chunks, _, error, events, _ = await self._stream(refused)
        self.assertIsInstance(error, LightCallFailed)
        self.assertEqual((len(attempts), sleep.call_count), (1, 0), "one attempt, no retry")
        self.assertEqual([e["reason"] for e in events if e["kind"] == "light_fallback"], [REFUSED])
        self.assertEqual(chunks, [])

    async def test_why_it_failed(self):
        for error, reason in ((Exception("429 RESOURCE_EXHAUSTED: quota"), REFUSED),
                              (Exception("429 Too Many Requests"), REFUSED), (RuntimeError("boom"), ERROR)):
            with self.subTest(error=str(error)):
                _, _, failed, events, _ = await self._stream(self._streamed(error))
                self.assertIsInstance(failed, LightCallFailed)
                self.assertEqual([e["reason"] for e in events if e["kind"] == "light_fallback"], [reason])

    async def test_it_ends_the_stream_it_reads_before_it_hands_over(self):
        """The rest of the reply is never used: the stream is closed at once, not when it's collected."""
        log = []

        async def wrapped(self, messages, stop=None, run_manager=None, **kwargs):
            try:
                yield _chunk("<speech>On it.</speech>")
                yield _chunk("More words.")
            finally:
                log.append("closed")

        async def run():
            with patch.object(RetryingChatGoogleGenerativeAI, "_astream", wrapped):
                async for chunk in _light().astream([HumanMessage("Any lights on?")]):
                    log.append(chunk.tool_call_chunks[0]["name"] if chunk.tool_call_chunks else chunk.content)

        await asyncio.create_task(run(), context=contextvars.copy_context())
        self.assertEqual([entry for entry in log if entry], ["<speech>On it.</speech>", "closed", HANDOVER])

    async def test_late_it_falls_back(self):
        closed = []

        async def hangs(self, messages, stop=None, run_manager=None, **kwargs):
            try:
                await asyncio.sleep(5)
                yield _chunk("<speech>Too late.</speech>")
            finally:
                closed.append(True)

        with patch.object(handover_module, "DEADLINE_S", 0.05):
            chunks, _, error, events, _ = await self._stream(hangs)
        self.assertIsInstance(error, LightCallFailed)
        self.assertEqual(chunks, [])
        (event,) = [e for e in events if e["kind"] == "light_fallback"]
        self.assertEqual(event["reason"], DEADLINE)
        self.assertLess(event["ms"], 1000)
        self.assertEqual(closed, [True], "its request is ended")

    async def _not_streamed(self, generate):
        out = io.StringIO()
        with patch.object(ChatGoogleGenerativeAI, "_agenerate", generate), contextlib.redirect_stdout(out):
            return await _light()._agenerate([HumanMessage("Are the lights on?")])

    async def test_a_reply_not_streamed(self):
        async def generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=[
                {"type": "thinking", "thinking": "The lights: check them."},
                {"type": "text", "text": "<speech>Checking.</speech>They're all off."}]))])

        message = (await self._not_streamed(generate)).generations[0].message
        self.assertEqual(message.content, "<speech>Checking.</speech>")
        self.assertEqual([(c["name"], c["args"]) for c in message.tool_calls], [(HANDOVER, {})])

    async def test_a_reply_not_streamed_falls_back_too(self):
        async def no_speech(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="They're all off."))])

        async def slow(self, messages, stop=None, run_manager=None, **kwargs):
            await asyncio.sleep(5)

        for generate in (no_speech, slow):
            with self.subTest(generate=generate.__name__), patch.object(handover_module, "DEADLINE_S", 0.05):
                with self.assertRaises(LightCallFailed):
                    await self._not_streamed(generate)

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

    def test_one_attempt_whatever_it_was_built_with(self):
        self.assertEqual(LightChatModel(model=FLASH_LITE, google_api_key="fake", max_retries=6)._attempts(), 1)


if __name__ == "__main__":
    unittest.main()
