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
  prefill). Gemini 3.5 Flash-Lite and 3.6 Flash refuse a request that ends on
  the model's turn, so for them a user turn follows, asking the model to go on
  from exactly where it stopped (`resume_request`). Only the new chunks are
  yielded, so the aggregated AIMessage and the Discord message read as one
  uninterrupted answer; the ask itself is never kept.
* 503 after a tool-call or other non-text chunk was streamed -> not resumable,
  the error propagates as it always has.
* Anything that is not a 503 propagates untouched.

The SDK keeps retrying every other retriable status (408/429/500/502/504);
only 503 is taken away from it, so there is one owner per status code. Both
use `max_retries` as the attempt count and the SDK's backoff curve.

Every 503 is logged, and each retry dispatches an `llm_retry` custom event
that the stream handler turns into a reaction on the triggering Discord message.

The SDK's own retries -- those statuses, and a dropped connection -- it logs
only at INFO, which nothing shows, so they used to pass as plain latency.
`_SdkRetries` prints each one too. Every retry, the SDK's and ours, is also a
`model_retry` event on the voice turn's trace.

The stall guard, on voice turns: a call that has no first chunk by its
tier's deadline (STALL_DEADLINE_S, well past its usual wait, so thinking
doesn't set it off) is sent a second time, and whichever of the two streams
first is used; the other is cancelled. Calls hang now and then -- over 20 s
for ~1.4% of FLASH's short replies and ~8.7% of PRO's in the month to Nov 2
-- and a second request usually answers at once. It never makes a call
slower, and it costs the second request's input tokens, only for calls that
hang. Not after a 429 on the same call; `LLM_STALL_GUARD=false` turns it
off. Each one is a `model_hedge` event on the trace.

Every response also passes through here on its way out, so this is where the
model's `<next_thinking>` pick is read and taken out of the text, streamed or
not (core/runtime/model/thinking.py).
"""

import asyncio
import logging
import random
import re
import time
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple

from google.genai.types import HttpOptions, HttpRetryOptions
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_google_genai import ChatGoogleGenerativeAI

from core.config.models import FLASH, FLASH_LITE, PRO
from core.config.settings import Config
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

# What a resume asks, after the text so far, of a model that refuses a request
# ending on its own turn (`resume_request`).
RESUME_ASK = ("Your reply above was cut off. Continue it from exactly where it stopped, "
              "without repeating anything and without a preamble.")

# The tiers that refuse a request ending on their own turn: the client
# (langchain-google-genai) raises "does not support model prefilling" before
# sending. Its own list is private; TestResumeWithoutPrefill holds this one to
# what the client accepts.
_NO_PREFILL = (FLASH, FLASH_LITE)


def _refuses_prefill(model: str) -> bool:
    """By name prefix ("models/gemini-3.6-flash-001" too): a wrong match only
    adds the ask, which every model accepts; a missed one fails the resume."""
    name = (model or "").lower().rsplit("/", 1)[-1]
    return name.startswith(_NO_PREFILL)


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


# Where the SDK logs its retries, at INFO.
SDK_LOGGER = "google_genai._api_client"
# tenacity's line: "Retrying <fn> in 1.42 seconds as it raised ClientError: 429 RESOURCE_EXHAUSTED. {...}."
_SDK_RETRY = re.compile(r" in (?P<wait>[\d.]+) seconds as it raised \w+: (?P<status>\d{3})\b")


class _SdkRetries(logging.Filter):
    """Prints each retry the SDK makes by itself, and records it on the voice
    turn's trace as a `model_retry` of the latest call. Two kinds: a retriable
    status, with the wait it chose; and a dropped connection ("Retrying due to
    aiohttp error: ..."), logged after the 1-10 s it sleeps first, so with no
    wait to report. Other records pass untouched."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if not message.startswith("Retrying"):
            return True
        match = _SDK_RETRY.search(message)
        status = int(match["status"]) if match else None
        wait = float(match["wait"]) if match else None
        reason = message[match.start("status"):] if match else message.removeprefix("Retrying due to ")
        trace = current_turn_trace()
        if trace is not None:
            trace.event("model_retry", call=trace.calls, by="sdk", status=status, wait_s=wait)
        waiting = f" retry_in={wait:.1f}s" if wait is not None else ""
        print(f"[LLMRetry] SDK retry agent={_agent_id()}{waiting}: {reason[:300]}")
        return False  # printed above; at INFO, nothing else would show it


def _watch_sdk_retries() -> None:
    """Puts _SdkRetries on the SDK's logger, once per process, and has that
    logger make its INFO records, which is where the retries are."""
    log = logging.getLogger(SDK_LOGGER)
    if any(type(f).__name__ == _SdkRetries.__name__ for f in log.filters):
        return
    log.addFilter(_SdkRetries())
    if log.getEffectiveLevel() > logging.INFO:
        log.setLevel(logging.INFO)


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


def resume_request(messages: List[BaseMessage], partial: str, model: str) -> List[BaseMessage]:
    """The request that continues a reply a 503 cut off after `partial`.

    The text so far goes in as the model's own turn. A model that takes a
    prefill writes on from it as it is. One that refuses a request ending on
    its own turn (`_NO_PREFILL`) gets a user turn after it, asking it to go on
    from exactly where it stopped. Either way only the new text is yielded,
    after the seam is trimmed (`_trim_seam`).
    """
    request = [*messages, AIMessage(content=partial)]
    if _refuses_prefill(model):
        request.append(HumanMessage(content=RESUME_ASK))
    return request


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


def _traced_call() -> Tuple[Optional[Any], Optional[int]]:
    """The voice turn's trace and the number of the model call `_with_thinking` just
    recorded on it, for that call's reply events; (None, None) outside a voice turn."""
    trace = current_turn_trace()
    return (trace, trace.calls) if trace is not None else (None, None)


# --- the stall guard ------------------------------------------------------------

# Seconds a call may go without its first chunk before the stall guard sends it
# again. Well past each tier's usual wait on a voice turn -- the first call's
# p90 was ~5 s on FLASH and ~12.6 s on PRO (low thinking), FLASH_LITE's short
# replies came in ~1 s -- so it's a hang that sets it off, not thinking.
STALL_DEADLINE_S: Dict[str, float] = {FLASH_LITE: 5.0, FLASH: 8.0, PRO: 20.0}


def _stall_deadline(model: str) -> Optional[float]:
    """The stall guard's deadline for a call to `model`, or None where it's off:
    switched off (LLM_STALL_GUARD), outside a voice turn, or a model with no
    deadline."""
    if not Config().llm_stall_guard:
        return None
    ctx = try_context()
    if ctx is None or ctx.get_surface() != "voice":
        return None
    return STALL_DEADLINE_S.get(str(model).removeprefix("models/"))


def _rate_limited(trace: Optional[Any], call: Optional[int]) -> bool:
    """True once the SDK has retried this call for a 429 (`_SdkRetries` traced
    it): a second request would only add to the load it was refused for."""
    return trace is not None and any(e.get("kind") == "model_retry" and e.get("status") == 429
                                     and e.get("call") == call for e in trace.events)


async def _next(stream: AsyncIterator[ChatGenerationChunk]) -> ChatGenerationChunk:
    return await stream.__anext__()


async def _close(stream: AsyncIterator[ChatGenerationChunk]) -> None:
    """Closes a racer's stream, and so its request. Best effort: a loser that
    won't close mustn't fail the turn the winner is answering."""
    try:
        await stream.aclose()
    except Exception:
        pass


async def _first_of(start: Callable[[], AsyncIterator[ChatGenerationChunk]], deadline: float,
                    may_hedge: Callable[[], bool], hedged: Callable[[], None]
                    ) -> Tuple[AsyncIterator[ChatGenerationChunk], Optional[ChatGenerationChunk], int]:
    """The stall guard's race. Starts a stream; if it has no first chunk within
    `deadline` seconds and `may_hedge()`, calls `hedged()` and starts the same
    request again, and keeps whichever streams its first chunk first.

    Returns that stream, its first chunk (None when it ended without one) and
    which request it was (0 the first, 1 the second). The other request is
    cancelled and closed, as both are if this is cancelled (a turn spoken
    over). A racer that fails leaves the race to the other; when both fail,
    the first one's error is raised, for the 503 retry to judge.

    A stream waiting for its first chunk can't be closed while it waits, so
    each wait runs as a task: cancelled first, then its stream closed.
    """
    streams = [start()]
    waiting: Dict[asyncio.Task, int] = {asyncio.create_task(_next(streams[0])): 0}
    errors: Dict[int, BaseException] = {}
    winner: Optional[int] = None
    try:
        await asyncio.wait(list(waiting), timeout=deadline)
        if not any(task.done() for task in waiting) and may_hedge():
            hedged()
            streams.append(start())
            waiting[asyncio.create_task(_next(streams[1]))] = 1
        while waiting:
            done, _ = await asyncio.wait(list(waiting), return_when=asyncio.FIRST_COMPLETED)
            for task in sorted(done, key=waiting.get):  # a tie goes to the first request
                index = waiting.pop(task)
                error = task.exception()
                if error is None or isinstance(error, StopAsyncIteration):
                    winner = index
                    return streams[index], (task.result() if error is None else None), index
                errors[index] = error
        raise errors[min(errors)]
    finally:
        for task in waiting:
            task.cancel()
        await asyncio.gather(*waiting, return_exceptions=True)
        for index, stream in enumerate(streams):
            if index != winner:
                await _close(stream)


class RetryingChatGoogleGenerativeAI(ChatGoogleGenerativeAI):
    """ChatGoogleGenerativeAI that hides 503s from callers. See module docstring."""

    def _attempts(self) -> int:
        # `max_retries=0` means "SDK default" to the Google client; match that.
        return self.max_retries if self.max_retries and self.max_retries > 0 else _SDK_DEFAULT_ATTEMPTS

    def _without_sdk_503_retry(self, kwargs: dict) -> dict:
        _watch_sdk_retries()  # whichever retries the SDK keeps, they show
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
        trace = current_turn_trace()
        if trace is not None:
            trace.event("model_retry", call=trace.calls, by="wrapper", status=503, wait_s=round(delay, 2), phase=phase)
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
        trace, call = _traced_call()
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
            if trace is not None:
                trace.model_reply(call, "done")
            return _picked(result)

    def _sdk_stream(self, request: List[BaseMessage], stop: Optional[List[str]], run_manager: Any,
                    kwargs: dict) -> AsyncIterator[ChatGenerationChunk]:
        """One request's stream, from the SDK."""
        return super()._astream(request, stop=stop, run_manager=run_manager, **kwargs)

    async def _guarded(self, request: List[BaseMessage], stop: Optional[List[str]], run_manager: Any,
                       kwargs: dict, trace: Optional[Any], call: Optional[int]) -> AsyncIterator[ChatGenerationChunk]:
        """The SDK's stream for `request`, through the stall guard when it's on
        for this call (`_stall_deadline`, `_first_of`).

        Not when a run manager is passed: the SDK's stream would fire its
        callbacks for a racer's chunk that may lose. LangChain passes none, and
        fires them itself on what this yields, so only the winner's text ever
        reaches the channel or the speech."""
        deadline = None if run_manager is not None else _stall_deadline(self.model)
        if deadline is None:
            async for gen in self._sdk_stream(request, stop, run_manager, kwargs):
                yield gen
            return

        sent = False

        def hedged() -> None:
            nonlocal sent
            sent = True
            print(f"[LLMRetry] stall guard: no first chunk after {deadline:g}s agent={_agent_id()} "
                  f"model={self.model}; sent it again")
            if trace is not None:
                trace.event("model_hedge", call=call, phase="sent", after_s=deadline)

        stream, first, which = await _first_of(lambda: self._sdk_stream(request, stop, run_manager, kwargs),
                                               deadline, lambda: not _rate_limited(trace, call), hedged)
        if sent:
            by = "second" if which else "first"
            print(f"[LLMRetry] stall guard: the {by} request answered first agent={_agent_id()} model={self.model}")
            if trace is not None:
                trace.event("model_hedge", call=call, phase="won", by=by)
        try:
            if first is not None:
                yield first
                async for gen in stream:
                    yield gen
        finally:
            await _close(stream)

    async def _astream(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Any = None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        kwargs = self._with_thinking(self._without_sdk_503_retry(kwargs))
        trace, call = _traced_call()
        replied = False     # its first chunk recorded on the trace, once, retries included
        attempt, waited = 1, 0.0
        partial = ""        # text already yielded, across attempts
        resumable = True    # False once a non-text chunk has been yielded
        trim_pending = False
        marker = MarkerFilter()  # the model's <next_thinking> pick, taken out as it streams
        while True:
            request = resume_request(messages, partial, self.model) if partial else messages
            try:
                async for gen in self._guarded(request, stop, run_manager, kwargs, trace, call):
                    if trace is not None and not replied:
                        trace.model_reply(call, "first_chunk")
                        replied = True
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
                if trace is not None:
                    trace.model_reply(call, "done")
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
