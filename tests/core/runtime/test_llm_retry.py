import os
import sys
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from google.genai.errors import ServerError, ClientError
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk, ChatGeneration, ChatResult
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError

from core.runtime import llm_retry
from core.runtime.llm_retry import RetryingChatGoogleGenerativeAI, is_503_error, backoff_delay


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
        from core.runtime.stream_handler import StreamHandler, EVENT_REACTION, LLM_RETRY_REACTION

        class G:
            async def astream_events(self, inputs, config=None, version=None):
                yield {"event": "on_custom_event", "name": LLM_RETRY_REACTION,
                       "data": {"agent_id": "main", "emoji": "🔁"}}

        events = [e async for e in StreamHandler.stream_graph_events(G(), {}, {})]
        self.assertEqual(events, [{"type": EVENT_REACTION, "agent_id": "main", "emoji": "🔁"}])


if __name__ == "__main__":
    unittest.main()
