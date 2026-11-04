import asyncio
import logging
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

import httpx
from google import genai
from google.genai.errors import ServerError, ClientError
from google.genai.types import HttpOptions, HttpRetryOptions
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk, ChatGeneration, ChatResult
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError

from core.config.models import FLASH, FLASH_LITE, PRO
from core.runtime.foundation.context import current_execution_context
from core.runtime.model import retry as llm_retry
from core.runtime.model.retry import RetryingChatGoogleGenerativeAI, is_503_error, backoff_delay
from core.runtime.observability.turn_trace import start_turn_trace


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


class TestResumeWithoutPrefill(unittest.IsolatedAsyncioTestCase):
    """Gemini 3.5 Flash-Lite and 3.6 Flash refuse a request that ends on the
    model's own turn, so a reply cut off mid-stream used to fail the turn after
    the backoff ("does not support model prefilling"): 5 of the matrix's 234
    FLASH and FLASH_LITE runs on Nov 3. The continuation now ends on a user
    turn asking the model to go on."""

    def setUp(self):
        self.sleep = patch.object(llm_retry, "_async_sleep", new=AsyncMock()).start()
        patch.object(llm_retry, "_announce_retry", new=AsyncMock()).start()
        self.addCleanup(patch.stopall)

    async def _run(self, model, script):
        llm = RetryingChatGoogleGenerativeAI(model=model, google_api_key="fake", max_retries=6)
        calls = []
        patch.object(ChatGoogleGenerativeAI, "_astream", _scripted_stream(script, calls)).start()
        out = [gen async for gen in llm._astream([HumanMessage(content="hi")])]
        return "".join(g.text for g in out), calls

    async def test_flash_and_flash_lite_are_asked_to_go_on(self):
        for model in (FLASH, FLASH_LITE):
            with self.subTest(model=model):
                text, calls = await self._run(model, [[_text("The first half, "), _503()],
                                                      [_text("and the second half.")]])
                self.assertEqual(text, "The first half, and the second half.")
                *sent, partial, ask = calls[1]["messages"]
                self.assertEqual(sent, calls[0]["messages"])
                self.assertEqual((type(partial), partial.content), (AIMessage, "The first half, "))
                self.assertEqual((type(ask), ask.content), (HumanMessage, llm_retry.RESUME_ASK))

    async def test_a_continuation_that_repeats_the_end_is_trimmed(self):
        first = "Preheat the oven to 200C. Then chop the onions finely. "
        text, _ = await self._run(FLASH, [[_text(first), _503()],
                                          [_text("Then chop the onions finely. Fry them gently.")]])
        self.assertEqual(text, first + "Fry them gently.")

    async def test_pro_still_writes_on_from_the_text_itself(self):
        _, calls = await self._run(PRO, [[_text("The first half, "), _503()], [_text("and the rest.")]])
        self.assertIsInstance(calls[1]["messages"][-1], AIMessage)

    def test_which_models_take_a_prefill(self):
        for model in (FLASH, FLASH_LITE, f"models/{FLASH}-001"):
            with self.subTest(model=model):
                self.assertTrue(llm_retry._refuses_prefill(model))
        for model in (PRO, "gemini-2.5-flash", ""):
            with self.subTest(model=model):
                self.assertFalse(llm_retry._refuses_prefill(model))

    def test_the_list_is_what_the_client_accepts(self):
        """The client checks before anything is sent, so each tier can be met
        here: it accepts its resume, and refuses a prefill only if listed. A
        change in langchain-google-genai's own (private) list shows up here."""
        history = [HumanMessage(content="hi")]
        prefill = [*history, AIMessage(content="The first half, ")]
        for model in (FLASH, FLASH_LITE, PRO):
            with self.subTest(model=model):
                client = ChatGoogleGenerativeAI(model=model, google_api_key="fake")
                client._prepare_request(llm_retry.resume_request(history, "The first half, ", model))
                if llm_retry._refuses_prefill(model):
                    with self.assertRaisesRegex(ValueError, "prefilling"):
                        client._prepare_request(prefill)
                else:
                    client._prepare_request(prefill)


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


class TestTurnTraceSpans(unittest.IsolatedAsyncioTestCase):
    """Each model call's round trip on the voice turn's trace: sent, first chunk, done."""

    def setUp(self):
        self.llm = RetryingChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key="fake", max_retries=6)
        patch.object(llm_retry, "_async_sleep", new=AsyncMock()).start()
        patch.object(llm_retry, "_announce_retry", new=AsyncMock()).start()
        self.addCleanup(patch.stopall)

    @staticmethod
    def spans(trace):
        return [(e["kind"], e.get("call"), e.get("phase")) for e in trace.events]

    async def test_a_streamed_call_records_its_first_chunk_and_its_end_once_across_a_retry(self):
        trace = start_turn_trace("day-planner", 1)
        script = [[_text("The first half, "), _503()], [_text("and the second.")]]
        patch.object(ChatGoogleGenerativeAI, "_astream", _scripted_stream(script, [])).start()
        out = [gen async for gen in self.llm._astream([HumanMessage(content="hi")])]

        self.assertEqual("".join(g.text for g in out), "The first half, and the second.")
        self.assertEqual(self.spans(trace), [("model_call", 1, None), ("model_reply", 1, "first_chunk"),
                                             ("model_retry", 1, "mid-stream"), ("model_reply", 1, "done")])
        [retry] = [e for e in trace.events if e["kind"] == "model_retry"]
        self.assertEqual((retry["by"], retry["status"]), ("wrapper", 503))

    async def test_a_whole_call_records_its_end(self):
        trace = start_turn_trace("day-planner", 1)
        result = ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])
        with patch.object(ChatGoogleGenerativeAI, "_agenerate", AsyncMock(return_value=result)):
            await self.llm._agenerate([HumanMessage(content="hi")])
        self.assertEqual(self.spans(trace), [("model_call", 1, None), ("model_reply", 1, "done")])


class TestTheSdksOwnRetriesShow(unittest.IsolatedAsyncioTestCase):
    """The SDK retries 408/429/500/502/504 and dropped connections by itself, and logs
    them only at INFO: each one is printed, like a 503, and goes on the voice trace."""

    def setUp(self):
        self.watch = llm_retry._SdkRetries()
        self.printed = patch("builtins.print").start()
        self.addCleanup(patch.stopall)

    @staticmethod
    def record(message):
        return logging.LogRecord(llm_retry.SDK_LOGGER, logging.INFO, __file__, 1, message, None, None)

    async def test_a_retried_status_is_printed_and_traced(self):
        trace = start_turn_trace("home-steward", 1)
        trace.model_call("home-steward", "gemini-x", "low")
        shown = self.watch.filter(self.record(
            "Retrying google.genai._api_client.BaseApiClient._async_request_once in 1.42 seconds as it raised "
            "ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'message': 'Quota exceeded'}}."))

        self.assertFalse(shown, "printed instead")
        line = self.printed.call_args.args[0]
        self.assertTrue(line.startswith("[LLMRetry] SDK retry agent="), line)
        self.assertIn("retry_in=1.4s: 429 RESOURCE_EXHAUSTED. {'error'", line)
        [event] = [e for e in trace.events if e["kind"] == "model_retry"]
        self.assertEqual({key: event[key] for key in ("call", "by", "status", "wait_s")},
                         {"call": 1, "by": "sdk", "status": 429, "wait_s": 1.42})

    async def test_a_dropped_connection_has_no_status_and_no_known_wait(self):
        trace = start_turn_trace("home-steward", 1)
        self.watch.filter(self.record("Retrying due to aiohttp error: Server disconnected"))

        self.assertTrue(self.printed.call_args.args[0].endswith(": aiohttp error: Server disconnected"))
        [event] = [e for e in trace.events if e["kind"] == "model_retry"]
        self.assertEqual((event["status"], event["wait_s"]), (None, None))

    async def test_outside_a_voice_turn_it_is_printed_all_the_same(self):
        self.watch.filter(self.record("Retrying due to aiohttp error: Server disconnected"))
        self.printed.assert_called_once()

    async def test_anything_else_the_sdk_logs_passes_untouched(self):
        self.assertTrue(self.watch.filter(self.record("The user provided project/location will take precedence")))
        self.printed.assert_not_called()

    def test_the_first_call_turns_it_on_once(self):
        log = logging.getLogger(llm_retry.SDK_LOGGER)
        filters, level = log.filters[:], log.level
        self.addCleanup(setattr, log, "filters", filters)
        self.addCleanup(log.setLevel, level)
        log.filters = [f for f in filters if not isinstance(f, llm_retry._SdkRetries)]
        log.setLevel(logging.WARNING)

        llm = RetryingChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key="fake")
        llm._without_sdk_503_retry({})
        llm._without_sdk_503_retry({"http_options": "the caller's own"})

        self.assertEqual(sum(isinstance(f, llm_retry._SdkRetries) for f in log.filters), 1)
        self.assertTrue(log.isEnabledFor(logging.INFO), "the SDK logs its retries at INFO")

    def test_the_sdks_own_words_are_read_from_a_real_retry(self):
        """Through the SDK's real retry, so a change in how it words one fails here."""
        llm_retry._watch_sdk_retries()
        replies = [httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                                       "message": "Quota exceeded"}}),
                   httpx.Response(200, json={"candidates": [{"content": {"role": "model",
                                                                         "parts": [{"text": "ready"}]}}]})]
        client = genai.Client(api_key="fake", http_options=HttpOptions(
            client_args={"transport": httpx.MockTransport(lambda request: replies.pop(0))},
            retry_options=HttpRetryOptions(attempts=2, initial_delay=0.01, max_delay=0.01)))

        self.assertEqual(client.models.generate_content(model="gemini-x", contents="hi").text, "ready")
        line = self.printed.call_args.args[0]
        self.assertTrue(line.startswith("[LLMRetry] SDK retry agent="), line)
        self.assertIn("retry_in=0.0s: 429 RESOURCE_EXHAUSTED", line)


def _stalls(*scripts):
    """A stand-in for the SDK's stream: each request sent takes the next script,
    (seconds before its first chunk, or an asyncio.Event to wait for; [texts,
    or an exception to raise]). Also returns a log of how many requests were
    sent and which streams ended: finished, failed, cancelled or closed."""
    log = {"sent": 0, "ended": []}

    def fake(self, messages, stop=None, run_manager=None, **kwargs):
        index = log["sent"]
        log["sent"] += 1
        delay, items = scripts[index]

        async def stream():
            try:
                await (delay.wait() if isinstance(delay, asyncio.Event) else asyncio.sleep(delay))
                for item in items:
                    if isinstance(item, BaseException):
                        raise item
                    yield _text(item)
            finally:
                log["ended"].append(index)
        return stream()
    return fake, log


class TestStallDeadlines(unittest.TestCase):

    def test_each_tier_waits_well_past_its_usual_first_chunk(self):
        self.assertEqual(llm_retry.STALL_DEADLINE_S, {FLASH_LITE: 5.0, FLASH: 8.0, PRO: 20.0})

    def test_only_voice_turns_are_guarded_and_the_switch_turns_it_off(self):
        on = lambda surface: patch.object(llm_retry, "try_context",
                                          return_value=SimpleNamespace(get_surface=lambda: surface))
        with on("voice"):
            self.assertEqual(llm_retry._stall_deadline(f"models/{FLASH}"), 8.0)
            self.assertIsNone(llm_retry._stall_deadline("gemini-pinned-version"))
            with patch.object(llm_retry, "Config", return_value=SimpleNamespace(llm_stall_guard=False)):
                self.assertIsNone(llm_retry._stall_deadline(FLASH))
        with on("text"):
            self.assertIsNone(llm_retry._stall_deadline(FLASH))
        with patch.object(llm_retry, "try_context", return_value=None):
            self.assertIsNone(llm_retry._stall_deadline(FLASH))


class TestStallGuard(unittest.IsolatedAsyncioTestCase):
    """On a voice turn, a call with no first chunk by its deadline is sent again:
    the first of the two to answer is used, and the other is cancelled."""

    DEADLINE = 0.05

    def setUp(self):
        self.llm = RetryingChatGoogleGenerativeAI(model="gemini-x", google_api_key="fake", max_retries=6)
        patch.object(llm_retry, "STALL_DEADLINE_S", {"gemini-x": self.DEADLINE}).start()
        patch.object(llm_retry, "resolve_thinking_level", lambda model: None).start()
        patch.object(llm_retry, "_async_sleep", new=AsyncMock()).start()
        patch.object(llm_retry, "_announce_retry", new=AsyncMock()).start()
        self.printed = patch("builtins.print").start()
        self.addCleanup(patch.stopall)

    async def asyncSetUp(self):
        self.trace = start_turn_trace("home-steward", 1)
        self.on("voice")

    @staticmethod
    def on(surface):
        current_execution_context.set(SimpleNamespace(agent_id="home-steward", get_surface=lambda: surface))

    async def answer(self, *scripts, **kwargs):
        fake, log = _stalls(*scripts)
        patch.object(ChatGoogleGenerativeAI, "_astream", fake).start()
        started = asyncio.get_running_loop().time()
        text = "".join([gen.text async for gen in self.llm._astream([HumanMessage(content="hi")], **kwargs)])
        log["seconds"] = asyncio.get_running_loop().time() - started
        return text, log

    def hedges(self):
        return [(e["phase"], e.get("by")) for e in self.trace.events if e["kind"] == "model_hedge"]

    async def test_an_answer_before_the_deadline_is_the_only_request(self):
        text, log = await self.answer((0, ["Hel", "lo"]))
        self.assertEqual((text, log["sent"]), ("Hello", 1))
        self.assertEqual(self.hedges(), [])

    async def test_a_hang_is_sent_again_and_the_first_to_answer_wins(self):
        text, log = await self.answer((5, ["never"]), (0, ["Fast", " answer"]))
        self.assertEqual(text, "Fast answer")
        self.assertLess(log["seconds"], 1, "the answer doesn't wait for the hung request")
        self.assertEqual(log["sent"], 2)
        self.assertEqual(sorted(log["ended"]), [0, 1], "the hung request is cancelled, not left open")
        self.assertEqual(self.hedges(), [("sent", None), ("won", "second")])
        [sent] = [e for e in self.trace.events if e["kind"] == "model_hedge" and e["phase"] == "sent"]
        self.assertEqual(sent["after_s"], self.DEADLINE)
        self.assertEqual([e["kind"] for e in self.trace.events].count("model_reply"), 2, "one first chunk, one end")

    async def test_the_first_request_still_wins_when_it_answers_first(self):
        text, log = await self.answer((0.1, ["First"]), (5, ["never"]))
        self.assertEqual(text, "First")
        self.assertLess(log["seconds"], 1, "nor for the second, once the first has answered")
        self.assertEqual(sorted(log["ended"]), [0, 1], "the second request is cancelled")
        self.assertEqual(self.hedges(), [("sent", None), ("won", "first")])

    async def test_a_tie_goes_to_the_first_request_and_the_second_is_closed(self):
        both = asyncio.Event()
        fake, log = _stalls((both, ["First"]), (both, ["Second"]))
        patch.object(ChatGoogleGenerativeAI, "_astream", fake).start()

        async def consume():
            return "".join([gen.text async for gen in self.llm._astream([HumanMessage(content="hi")])])

        task = asyncio.create_task(consume())
        while log["sent"] < 2:
            await asyncio.sleep(0.01)
        both.set()  # both answer at the same moment
        self.assertEqual(await task, "First")
        self.assertEqual(sorted(log["ended"]), [0, 1], "the second, already streaming, is closed, not left open")

    async def test_a_request_that_fails_leaves_the_race_to_the_other(self):
        text, _ = await self.answer((0.1, [RuntimeError("connection reset")]), (0.2, ["Second"]))
        self.assertEqual(text, "Second")

    async def test_when_both_fail_the_first_error_goes_to_the_503_retry(self):
        text, log = await self.answer((0.1, [_503()]), (0.15, [ValueError("second")]), (0, ["Retried"]))
        self.assertEqual(text, "Retried")
        self.assertEqual(log["sent"], 3, "two racers, then the 503 retry's request")

    async def test_when_both_fail_otherwise_the_first_error_propagates(self):
        with self.assertRaises(ValueError) as caught:
            await self.answer((0.1, [ValueError("first")]), (0.15, [ValueError("second")]))
        self.assertEqual(str(caught.exception), "first")

    async def test_a_reply_with_no_chunks_is_an_empty_answer(self):
        text, log = await self.answer((0, []))
        self.assertEqual((text, log["sent"]), ("", 1))

    async def test_a_turn_spoken_over_mid_race_cancels_both_requests(self):
        fake, log = _stalls((5, ["a"]), (5, ["b"]))
        patch.object(ChatGoogleGenerativeAI, "_astream", fake).start()

        async def consume():
            return [gen async for gen in self.llm._astream([HumanMessage(content="hi")])]

        task = asyncio.create_task(consume())
        await asyncio.sleep(self.DEADLINE * 3)
        self.assertEqual(log["sent"], 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(sorted(log["ended"]), [0, 1])

    async def test_off_outside_voice_turns(self):
        self.on("text")
        text, log = await self.answer((0.2, ["Slow but sure"]))
        self.assertEqual((text, log["sent"]), ("Slow but sure", 1))

    async def test_off_when_switched_off(self):
        with patch.object(llm_retry, "Config", return_value=SimpleNamespace(llm_stall_guard=False)):
            _, log = await self.answer((0.2, ["Slow"]))
        self.assertEqual(log["sent"], 1)

    async def test_off_when_a_run_manager_is_passed(self):
        _, log = await self.answer((0.2, ["Slow"]), run_manager=MagicMock())
        self.assertEqual(log["sent"], 1, "its callbacks would fire for a racer that may lose")

    async def test_not_after_a_429_on_the_same_call(self):
        self.trace.event("model_retry", call=1, by="sdk", status=429, wait_s=1.0)  # as _SdkRetries records it
        _, log = await self.answer((0.2, ["Slow"]))
        self.assertEqual(log["sent"], 1)


if __name__ == "__main__":
    unittest.main()
