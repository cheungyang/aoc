"""Transparent retry of Gemini 503 ("model is experiencing high demand") errors.

Why this exists, and why it lives at the model call rather than around the turn:

* The `google-genai` SDK already retries a 503, but only while *opening* the
  request. A 503 delivered mid-stream is never retried, and the SDK's own
  retries are silent -- nothing is logged.
* Re-running the whole graph turn would re-run tools that already executed
  (writes, Home Assistant calls, delegations). Retrying the single LLM call
  repeats only the step that failed.

Behaviour:

* 503 before anything was streamed -> back off and resend the same request.
* 503 after text was streamed -> back off and send a *continuation*: the
  original messages plus the partial text as a trailing model turn (a pure
  prefill). Only the new chunks are yielded, so the aggregated AIMessage and
  the Discord message read as one uninterrupted answer.
* 503 after a tool-call or other non-text chunk was streamed -> not resumable,
  the error propagates as it always has.
* Anything that is not a 503 propagates untouched.

The SDK keeps retrying every other retriable status (408/429/500/502/504);
only 503 is taken away from it, so there is one owner per status code. Both
use `max_retries` as the attempt count and the SDK's backoff curve.

Every 503 is logged, and each retry dispatches an `llm_retry` custom event
that the stream handler turns into a reaction on the triggering Discord message.

Every response also passes through here on its way out, so this is where the
model's `<next_thinking>` pick is read and taken out of the text, streamed or
not (core/runtime/model/thinking.py).
"""

import asyncio
import random
import time
from typing import Any, AsyncIterator, List, Optional

from google.genai.types import HttpOptions, HttpRetryOptions
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_google_genai import ChatGoogleGenerativeAI

from core.runtime.foundation import events
from core.runtime.foundation.context import try_context
from core.runtime.model.thinking import MarkerFilter, pick_thinking, resolve_thinking_level, take_marker
from core.runtime.observability.turn_trace import current_turn_trace

RETRY_EMOJI = "🔁"

# The SDK's default retriable codes, minus 503 (owned by this module).
_SDK_RETRY_CODES_EXCEPT_503 = [408, 429, 500, 502, 504]

# Mirrors the SDK's defaults (google.genai._api_client._RETRY_*), so a 503 is
# retried on the same curve it always was: ~1s, 2s, 4s, 8s, 16s (+ <=1s jitter).
_SDK_DEFAULT_ATTEMPTS = 5
_INITIAL_DELAY = 1.0
_MAX_DELAY = 60.0
_EXP_BASE = 2
_JITTER = 1.0

# Minimum length of a repeated tail before it is treated as a seam to trim.
_MIN_SEAM_OVERLAP = 20
_MAX_SEAM_SCAN = 500

# Indirection so tests can skip the real backoff.
_async_sleep = asyncio.sleep
_sync_sleep = time.sleep


def is_503_error(error: Any) -> bool:
    """True if `error`, or anything in its cause/context chain, is a 503.

    langchain-google-genai re-raises SDK errors as ChatGoogleGenerativeAIError
    `from` the original, so the chain has to be walked.
    """
    seen: List[Any] = []
    stack = [error]
    while stack:
        err = stack.pop()
        if err is None or any(err is s for s in seen):
            continue
        seen.append(err)
        for attr in ("code", "status_code"):
            if str(getattr(err, attr, "")) == "503":
                return True
        if getattr(err, "status", None) == "UNAVAILABLE":
            return True
        msg = str(err)
        if "high demand" in msg or ("503" in msg and ("UNAVAILABLE" in msg or "overloaded" in msg.lower())):
            return True
        if isinstance(err, BaseException):
            stack.extend([err.__cause__, err.__context__])
    return False


def backoff_delay(retry_index: int) -> float:
    """Delay before retry number `retry_index` (0-based), same shape as the SDK's
    tenacity `wait_exponential_jitter`."""
    return min(_INITIAL_DELAY * (_EXP_BASE ** retry_index) + random.uniform(0, _JITTER), _MAX_DELAY)


def _agent_id() -> Optional[str]:
    try:
        ctx = try_context()
        return getattr(ctx, "agent_id", None) if ctx else None
    except Exception:
        return None


async def _announce_retry() -> None:
    """Asks the channel to show the retry emoji. Best effort: outside a runnable
    context (e.g. a bare model call) there is nobody to tell."""
    try:
        from langchain_core.callbacks import adispatch_custom_event
        await adispatch_custom_event(events.LLM_RETRY_REACTION, {"agent_id": _agent_id(), "emoji": RETRY_EMOJI})
    except Exception:
        pass


def _is_text_only(gen: ChatGenerationChunk) -> bool:
    """A chunk is resumable-safe if it carries text (or nothing), never a tool
    call or a non-text content block."""
    msg = gen.message
    if getattr(msg, "tool_call_chunks", None) or getattr(msg, "tool_calls", None):
        return False
    content = msg.content
    if isinstance(content, str):
        return True
    return all(
        isinstance(b, str) or (isinstance(b, dict) and b.get("type") == "text")
        for b in content
    )


def _trim_seam(gen: ChatGenerationChunk, partial: str) -> ChatGenerationChunk:
    """Drops a repeated tail of `partial` from the start of the first
    continuation chunk. Only single-text-block chunks are rewritten."""
    text = gen.text
    top = min(len(text), len(partial), _MAX_SEAM_SCAN)
    overlap = next(
        (k for k in range(top, _MIN_SEAM_OVERLAP - 1, -1) if partial.endswith(text[:k])),
        0,
    )
    if not overlap:
        return gen
    content = gen.message.content
    remainder = text[overlap:]
    if isinstance(content, str):
        new_content: Any = remainder
    elif len(content) == 1 and isinstance(content[0], dict):
        new_content = [{**content[0], "text": remainder}]
    elif len(content) == 1 and isinstance(content[0], str):
        new_content = [remainder]
    else:
        return gen
    return ChatGenerationChunk(
        message=gen.message.model_copy(update={"content": new_content}),
        generation_info=gen.generation_info,
    )


def _map_text(content: Any, transform) -> Any:
    """`content` with each text part passed through `transform`; the same object if none changed."""
    if isinstance(content, str):
        return transform(content)
    changed, mapped = False, []
    for block in content:
        if isinstance(block, str):
            text = transform(block)
            changed |= text != block
            mapped.append(text)
        elif isinstance(block, dict) and block.get("type") == "text":
            text = transform(block.get("text", ""))
            changed |= text != block.get("text", "")
            mapped.append({**block, "text": text})
        else:
            mapped.append(block)
    return mapped if changed else content


def _without_marker(gen: ChatGenerationChunk, marker: MarkerFilter) -> ChatGenerationChunk:
    """A streamed chunk with the marker, and whatever may start one, held out of its text."""
    content = _map_text(gen.message.content, marker.feed)
    if content is gen.message.content or content == gen.message.content:
        return gen
    return ChatGenerationChunk(message=gen.message.model_copy(update={"content": content}),
                               generation_info=gen.generation_info)


def _picked(result: ChatResult) -> ChatResult:
    """A whole response without its marker, its pick recorded; the same result if it has none."""
    levels = []

    def take(text: str) -> str:
        text, level = take_marker(text)
        if level:
            levels.append(level)
        return text

    generations = []
    for gen in result.generations:
        content = _map_text(gen.message.content, take)
        generations.append(gen if content is gen.message.content else ChatGeneration(
            message=gen.message.model_copy(update={"content": content}), generation_info=gen.generation_info))
    if not levels:
        return result
    pick_thinking(levels[-1])
    return ChatResult(generations=generations, llm_output=result.llm_output)


class RetryingChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
    """ChatGoogleGenerativeAI that hides 503s from callers. See module docstring."""

    def _attempts(self) -> int:
        # `max_retries=0` means "SDK default" to the Google client; match that.
        return self.max_retries if self.max_retries and self.max_retries > 0 else _SDK_DEFAULT_ATTEMPTS

    def _without_sdk_503_retry(self, kwargs: dict) -> dict:
        if "http_options" in kwargs:
            return kwargs  # caller is managing transport options explicitly
        return {
            **kwargs,
            "http_options": HttpOptions(
                retry_options=HttpRetryOptions(
                    attempts=self._attempts(),
                    http_status_codes=_SDK_RETRY_CODES_EXCEPT_503,
                )
            ),
        }

    def _with_thinking(self, kwargs: dict) -> dict:
        """Adds the thinking level this call runs at (see core.runtime.model.thinking).

        A level the caller passed explicitly wins. Whatever the level, it is
        recorded on the voice turn's trace when there is one, so each model
        call's depth sits next to the latency it cost.
        """
        if "thinking_level" in kwargs or "reasoning_effort" in kwargs:
            level = kwargs.get("thinking_level", kwargs.get("reasoning_effort"))
        else:
            level = resolve_thinking_level(self.model)
            if level:
                kwargs = {**kwargs, "thinking_level": level}
        trace = current_turn_trace()
        if trace is not None:
            trace.model_call(_agent_id(), self.model, level)
        return kwargs

    def _retry_delay(self, error: Exception, attempt: int, phase: str, chars: int) -> Optional[float]:
        """Logs the failure; returns the delay before the next attempt, or None
        if the error must propagate."""
        if not is_503_error(error):
            return None
        total = self._attempts()
        agent = _agent_id()
        if attempt >= total:
            print(f"[LLMRetry] 503 giving up agent={agent} model={self.model} attempts={attempt}/{total} phase={phase} chars_so_far={chars}")
            return None
        delay = backoff_delay(attempt - 1)
        print(f"[LLMRetry] 503 agent={agent} model={self.model} attempt={attempt}/{total} phase={phase} chars_so_far={chars} retry_in={delay:.1f}s")
        return delay

    def _log_recovered(self, attempt: int, waited: float) -> None:
        if attempt > 1:
            print(f"[LLMRetry] recovered agent={_agent_id()} model={self.model} attempts={attempt} extra_wait={waited:.1f}s")

    def _generate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        kwargs = self._with_thinking(self._without_sdk_503_retry(kwargs))
        attempt, waited = 1, 0.0
        while True:
            try:
                result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            except Exception as e:
                delay = self._retry_delay(e, attempt, "request", 0)
                if delay is None:
                    raise
                _sync_sleep(delay)
                waited += delay
                attempt += 1
                continue
            self._log_recovered(attempt, waited)
            return _picked(result)

    async def _agenerate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        kwargs = self._with_thinking(self._without_sdk_503_retry(kwargs))
        attempt, waited = 1, 0.0
        while True:
            try:
                result = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
            except Exception as e:
                delay = self._retry_delay(e, attempt, "request", 0)
                if delay is None:
                    raise
                await _announce_retry()
                await _async_sleep(delay)
                waited += delay
                attempt += 1
                continue
            self._log_recovered(attempt, waited)
            return _picked(result)

    async def _astream(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Any = None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        kwargs = self._with_thinking(self._without_sdk_503_retry(kwargs))
        attempt, waited = 1, 0.0
        partial = ""        # text already yielded, across attempts
        resumable = True    # False once a non-text chunk has been yielded
        trim_pending = False
        marker = MarkerFilter()  # the model's <next_thinking> pick, taken out as it streams
        while True:
            request = [*messages, AIMessage(content=partial)] if partial else messages
            try:
                async for gen in super()._astream(request, stop=stop, run_manager=run_manager, **kwargs):
                    if trim_pending and gen.text:
                        gen = _trim_seam(gen, partial)
                        trim_pending = False
                    gen = _without_marker(gen, marker)
                    if _is_text_only(gen):
                        partial += gen.text
                    else:
                        resumable = False
                    yield gen
                held = marker.flush()
                if held:
                    partial += held
                    yield ChatGenerationChunk(message=AIMessageChunk(content=held))
                if marker.level:
                    pick_thinking(marker.level)
                self._log_recovered(attempt, waited)
                return
            except Exception as e:
                phase = "mid-stream" if partial or not resumable else "request"
                delay = self._retry_delay(e, attempt, phase, len(partial)) if resumable else None
                if delay is None:
                    if not resumable and is_503_error(e):
                        print(f"[LLMRetry] 503 after non-text output, not resumable agent={_agent_id()} model={self.model}")
                    raise
                await _announce_retry()
                await _async_sleep(delay)
                waited += delay
                attempt += 1
                trim_pending = bool(partial)
                marker.flush()  # the continuation regenerates from what was yielded
