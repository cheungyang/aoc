import os
import sys
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from google.genai.errors import ServerError, ClientError
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk, ChatGeneration, ChatResult
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError

from core.runtime.model import retry as llm_retry
from core.runtime.model.retry import RetryingChatGoogleGenerativeAI, is_503_error, backoff_delay


def _503():
    body = {"error": {"code": 503, "status": "UNAVAILABLE",
                      "message": "This model is currently experiencing high demand."}}
    try:
        raise ServerError(503, body)
    except ServerError as e:
        # langchain-google-genai wraps SDK errors like this
        try:
            raise ChatGoogleGenerativeAIError("Error calling model") from e
        except ChatGoogleGenerativeAIError as wrapped:
            return wrapped


def _429():
    return ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded"}})


def _text(t):
    return ChatGenerationChunk(message=AIMessageChunk(content=t))


def _tool_chunk():
    return ChatGenerationChunk(message=AIMessageChunk(
        content="", tool_call_chunks=[{"name": "x", "args": "{}", "id": "1", "index": 0}]))


def _scripted_stream(script, calls):
    """Each call to _astream consumes the next list in `script`; items that are
    exceptions are raised at that point in the stream."""
    async def fake(self, messages, stop=None, run_manager=None, **kwargs):
        calls.append({"messages": list(messages), "kwargs": kwargs})
        for item in script[len(calls) - 1]:
            if isinstance(item, BaseException):
                raise item
            yield item
    return fake


class TestIs503(unittest.TestCase):
    def test_wrapped_server_error(self):
        self.assertTrue(is_503_error(_503()))

    def test_high_demand_message(self):
        self.assertTrue(is_503_error(RuntimeError("[503] This model is currently experiencing high demand.")))

    def test_other_errors(self):
        self.assertFalse(is_503_error(_429()))
        self.assertFalse(is_503_error(ValueError("boom")))

    def test_backoff_matches_sdk_curve(self):
        for i, base in enumerate([1, 2, 4, 8, 16]):
            d = backoff_delay(i)
            self.assertGreaterEqual(d, base)
            self.assertLessEqual(d, base + 1)
        self.assertLessEqual(backoff_delay(20), 60)


class TestRetryingStream(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.llm = RetryingChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key="fake", max_retries=6)
        self.sleep = patch.object(llm_retry, "_async_sleep", new=AsyncMock()).start()
        self.announce = patch.object(llm_retry, "_announce_retry", new=AsyncMock()).start()
        self.addCleanup(patch.stopall)

    async def _run(self, script):
        calls = []
        patch.object(ChatGoogleGenerativeAI, "_astream", _scripted_stream(script, calls)).start()
        out = []
        async for gen in self.llm._astream([HumanMessage(content="hi")]):
            out.append(gen)
        return out, calls

    async def test_503_before_any_chunk_retries_same_request(self):
        out, calls = await self._run([[_503()], [_text("Hello"), _text(" world")]])
        self.assertEqual("".join(g.text for g in out), "Hello world")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["messages"], calls[0]["messages"])
        self.sleep.assert_awaited_once()
        self.announce.assert_awaited_once()

    async def test_mid_stream_503_continues_with_prefill(self):
        out, calls = await self._run([
            [_text("The first half, "), _503()],
            [_text("and the second half.")],
        ])
        self.assertEqual("".join(g.text for g in out), "The first half, and the second half.")
        cont = calls[1]["messages"]
        self.assertIsInstance(cont[-1], AIMessage)
        self.assertEqual(cont[-1].content, "The first half, ")
        self.assertEqual(cont[:-1], calls[0]["messages"])

    async def test_repeated_seam_is_trimmed(self):
        first = "Preheat the oven to 200C. Then chop the onions finely. "
        out, _ = await self._run([
            [_text(first), _503()],
            [_text("Then chop the onions finely. Fry them gently."), _text(" Done.")],
        ])
        self.assertEqual("".join(g.text for g in out), first + "Fry them gently. Done.")

    async def test_503_after_tool_call_chunk_propagates(self):
        with self.assertRaises(ChatGoogleGenerativeAIError):
            await self._run([[_tool_chunk(), _503()], [_text("never")]])
        self.sleep.assert_not_awaited()

    async def test_non_503_propagates_without_retry(self):
        with self.assertRaises(ClientError):
            await self._run([[_429()], [_text("never")]])
        self.sleep.assert_not_awaited()

    async def test_gives_up_after_max_retries(self):
        self.llm.max_retries = 3
        with self.assertRaises(ChatGoogleGenerativeAIError):
            await self._run([[_503()], [_503()], [_503()], [_text("never")]])
        self.assertEqual(self.sleep.await_count, 2)

    async def test_sdk_keeps_other_codes_but_not_503(self):
        _, calls = await self._run([[_text("ok")]])
        retry = calls[0]["kwargs"]["http_options"].retry_options
        self.assertEqual(retry.attempts, 6)
        self.assertNotIn(503, retry.http_status_codes)
        self.assertIn(429, retry.http_status_codes)


class TestRetryingGenerate(unittest.IsolatedAsyncioTestCase):
    async def test_agenerate_retries_503(self):
        llm = RetryingChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key="fake")
        result = ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])
        inner = AsyncMock(side_effect=[_503(), result])
        with patch.object(llm_retry, "_async_sleep", new=AsyncMock()), \
             patch.object(llm_retry, "_announce_retry", new=AsyncMock()), \
             patch.object(ChatGoogleGenerativeAI, "_agenerate", inner):
            got = await llm._agenerate([HumanMessage(content="hi")])
        self.assertIs(got, result)
        self.assertEqual(inner.await_count, 2)

    def test_generate_retries_503(self):
        llm = RetryingChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key="fake")
        result = ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])
        inner = MagicMock(side_effect=[_503(), result])
        with patch.object(llm_retry, "_sync_sleep"), \
             patch.object(ChatGoogleGenerativeAI, "_generate", inner):
            self.assertIs(llm._generate([HumanMessage(content="hi")]), result)


class TestRetryReactionEvent(unittest.IsolatedAsyncioTestCase):
    async def test_llm_retry_custom_event_becomes_reaction(self):
        from core.runtime.foundation.events import LLM_RETRY_REACTION, REACTION
        from core.runtime.streaming import StreamHandler

        class G:
            async def astream_events(self, inputs, config=None, version=None):
                yield {"event": "on_custom_event", "name": LLM_RETRY_REACTION,
                       "data": {"agent_id": "main", "emoji": "🔁"}}

        events = [e async for e in StreamHandler.stream_graph_events(G(), {}, {})]
        self.assertEqual(events, [{"type": REACTION, "agent_id": "main", "emoji": "🔁"}])


class TestNextThinkingMarker(unittest.IsolatedAsyncioTestCase):
    """The model's <next_thinking> pick is taken out of every response, streamed or not, and recorded."""

    def setUp(self):
        self.llm = RetryingChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key="fake", max_retries=6)
        self.pick = patch.object(llm_retry, "pick_thinking").start()
        patch.object(llm_retry, "_async_sleep", new=AsyncMock()).start()
        patch.object(llm_retry, "_announce_retry", new=AsyncMock()).start()
        self.addCleanup(patch.stopall)

    async def stream(self, *attempts):
        calls = []
        patch.object(ChatGoogleGenerativeAI, "_astream", _scripted_stream(list(attempts), calls)).start()
        return [gen async for gen in self.llm._astream([HumanMessage(content="hi")])], calls

    async def test_a_streamed_marker_split_across_chunks_is_taken_out_and_picked(self):
        out, _ = await self.stream([_text("<next_thin"), _text("king>high</next_thinking>\n"), _tool_chunk()])
        self.assertEqual("".join(g.text for g in out), "")
        self.assertTrue(any(g.message.tool_call_chunks for g in out), "the tool call still goes out")
        self.pick.assert_called_once_with("high")

    async def test_the_text_around_it_streams_on(self):
        out, _ = await self.stream([_text("Let me check.\n<next_thinking>medium</next_thinking>\n"),
                                    _text("One moment."), _tool_chunk()])
        self.assertEqual("".join(g.text for g in out), "Let me check.\nOne moment.")
        self.pick.assert_called_once_with("medium")

    async def test_text_blocks_too(self):
        block = ChatGenerationChunk(message=AIMessageChunk(content=[{"type": "text", "text": "<next_thinking>low</next_thinking>"}]))
        out, _ = await self.stream([block, _tool_chunk()])
        self.assertEqual("".join(g.text for g in out), "")
        self.pick.assert_called_once_with("low")

    async def test_without_a_marker_nothing_changes(self):
        chunks = [_text("Hello"), _text(" world")]
        out, _ = await self.stream(chunks)
        self.assertEqual(out, chunks)
        self.pick.assert_not_called()

    async def test_a_look_alike_held_at_the_end_still_goes_out(self):
        out, _ = await self.stream([_text("See <next_")])
        self.assertEqual("".join(g.text for g in out), "See <next_")
        self.pick.assert_not_called()

    async def test_a_resumed_stream_never_prefills_the_marker(self):
        out, calls = await self.stream([_text("<next_thinking>high</next_thinking>Half, "), _503()],
                                       [_text("and the rest.")])
        self.assertEqual("".join(g.text for g in out), "Half, and the rest.")
        self.assertEqual(calls[1]["messages"][-1].content, "Half, ")
        self.pick.assert_called_once_with("high")

    async def test_a_whole_response_is_cleaned_and_picked(self):
        result = ChatResult(generations=[ChatGeneration(message=AIMessage(
            content="<next_thinking>low</next_thinking>\nOk", tool_calls=[{"name": "x", "args": {}, "id": "1"}]))])
        with patch.object(ChatGoogleGenerativeAI, "_agenerate", AsyncMock(return_value=result)):
            got = await self.llm._agenerate([HumanMessage(content="hi")])
        self.assertEqual(got.generations[0].message.content, "Ok")
        self.assertEqual(got.generations[0].message.tool_calls[0]["name"], "x")
        self.pick.assert_called_once_with("low")

    async def test_a_whole_response_without_one_is_the_same_object(self):
        result = ChatResult(generations=[ChatGeneration(message=AIMessage(content="Ok"))])
        with patch.object(ChatGoogleGenerativeAI, "_agenerate", AsyncMock(return_value=result)):
            self.assertIs(await self.llm._agenerate([HumanMessage(content="hi")]), result)
        self.pick.assert_not_called()

    def test_the_sync_path_too(self):
        result = ChatResult(generations=[ChatGeneration(message=AIMessage(content="<next_thinking>high</next_thinking>"))])
        with patch.object(ChatGoogleGenerativeAI, "_generate", MagicMock(return_value=result)):
            self.assertEqual(self.llm._generate([HumanMessage(content="hi")]).generations[0].message.content, "")
        self.pick.assert_called_once_with("high")


if __name__ == "__main__":
    unittest.main()
