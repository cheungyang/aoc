"""The light first call's model (graphs/voice): it only says the turn's first line.

A voice turn's first model call can run on a light model, with no tools: in
about a second it says one short sentence in the agent's voice, while the
agent's own model works out the answer (graphs/voice/prompts/light_call.md).
It never answers. Every turn goes on to the agent's own model.

`LightChatModel` passes on only the reply's first <speech> block, with words
in it, and ends the call as soon as that block closes; anything else the
light model writes is dropped. Then it puts a `handover` tool call on the
reply: FLASH and FLASH_LITE refuse a request that ends on the model's own
turn, so the agent's model has to start after a function response, and
graphs/voice runs the call locally (its `handover` tool). No model ever has
it declared. A reply that breaks off after its words began still hands
over, its <speech> block closed.

It fails fast rather than wait: one attempt, no retries, and DEADLINE_S for
its first chunk (for the whole reply, when it isn't streamed). A call that's
refused, errors or misses the deadline before it said anything, or that
ends without words in a <speech> block, raises LightCallFailed, logged and
put on the voice trace as `light_fallback`: graphs/voice then makes the call
on the agent's model, as if there were no light call.
"""
import asyncio
import re
import time
import uuid
from typing import Any, ClassVar, Dict, List, Optional

from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.messages.tool import tool_call, tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from core.runtime.model.retry import RetryingChatGoogleGenerativeAI, _agent_id, is_503_error
from core.runtime.observability.turn_trace import current_turn_trace

HANDOVER = "handover"  # the tool call that follows the first line

# Seconds the light call has for its first chunk; for the whole reply when it
# isn't streamed. Its first chunk came in 0.70 s at p50 and 1.12 s at p90 in
# the benchmark, so only a call in trouble misses it.
DEADLINE_S = 1.5

# Why a light call fell back (its `light_fallback` event's `reason`).
DEADLINE = "deadline"    # no first chunk by DEADLINE_S
REFUSED = "refused"      # the API turned it away: 429, 503
ERROR = "error"          # any other failure before it said anything
NO_SPEECH = "no_speech"  # it ended without words in a <speech> block

SPEECH_OPEN, SPEECH_CLOSE = "<speech>", "</speech>"


class LightCallFailed(Exception):
    """The light call has nothing for the user to hear: the agent's model makes the call instead."""


def _tag_start(text: str, tag: str) -> str:
    """The end of `text` that may be the start of `tag`, split across chunks: held for the next one."""
    lower = text.lower()
    for n in range(min(len(tag) - 1, len(text)), 0, -1):
        if tag.startswith(lower[-n:]):
            return text[-n:]
    return ""


class OpenerFilter:
    """Passes on a reply's first <speech> block, and nothing else, as it streams.

    Text before `<speech>` is never passed on. The block goes out as it comes,
    once it has words: `<speech>` with the first of them, so a block that
    stays empty sends nothing. `said` turns True then, and `done` once
    `</speech>` has come; whatever follows is dropped. A tag split across
    chunks is held until it's whole, a few characters at most.
    """

    def __init__(self):
        self.opened = False  # `<speech>` came
        self.said = False    # words of the block went out
        self.done = False    # `</speech>` came: nothing after it goes out
        self._held = ""

    def feed(self, text: str) -> str:
        """What goes out now, of whatever was held plus `text`."""
        if self.done or not text:
            return ""
        buf, self._held = self._held + text, ""
        if not self.opened:
            at = buf.lower().find(SPEECH_OPEN)
            if at == -1:
                self._held = _tag_start(buf, SPEECH_OPEN)
                return ""
            self.opened, buf = True, buf[at + len(SPEECH_OPEN):]
        at = buf.lower().find(SPEECH_CLOSE)
        if at != -1:
            self.done, words = True, buf[:at]
        else:
            self._held = _tag_start(buf, SPEECH_CLOSE)
            words = buf[:len(buf) - len(self._held)]
        if not self.said:
            words = words.lstrip()
            if not words:
                return ""
            self.said, words = True, SPEECH_OPEN + words
        return words + (SPEECH_CLOSE if self.done else "")

    def close(self) -> str:
        """`</speech>`, for a block whose words began but that never closed; else nothing."""
        self._held = ""
        if self.said and not self.done:
            self.done = True
            return SPEECH_CLOSE
        return ""


def _text(content: Any) -> str:
    """The text parts of a message's content, joined."""
    if isinstance(content, str):
        return content
    return "".join(block if isinstance(block, str) else block.get("text", "") for block in content
                   if isinstance(block, str) or (isinstance(block, dict) and block.get("type") == "text"))


def _why(error: BaseException) -> str:
    """REFUSED when the API turned the call away (503, 429), else ERROR."""
    if is_503_error(error) or re.search(r"\b429\b|RESOURCE_EXHAUSTED", str(error)):
        return REFUSED
    return ERROR


def _call_id() -> str:
    return f"{HANDOVER}-{uuid.uuid4().hex[:12]}"


def _handover_chunk() -> ChatGenerationChunk:
    # Text-free: the call is the reply's last part, in the stream as in history.
    call = tool_call_chunk(name=HANDOVER, args="{}", id=_call_id(), index=0)
    return ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[call]))


def _with_text(gen: ChatGenerationChunk, text: str) -> ChatGenerationChunk:
    """`gen` with `text` as its whole content: its usage and metadata kept."""
    return ChatGenerationChunk(message=gen.message.model_copy(update={"content": text}),
                               generation_info=gen.generation_info)


class LightChatModel(RetryingChatGoogleGenerativeAI):
    """The light first call's model: one attempt, DEADLINE_S to start, and only
    the reply's first <speech> block, followed by a `handover` call. Nothing to
    say raises LightCallFailed. Its calls say `light` on the voice trace."""

    trace_fields: ClassVar[Dict[str, Any]] = {"light": True}

    def _attempts(self) -> int:
        # One attempt: neither the SDK nor the 503 retry tries it again.
        return 1

    def _fell_back(self, reason: str, detail: str, started: float) -> LightCallFailed:
        """Logs a light call that gave the turn nothing to say, and puts it on the trace."""
        ms = round((time.monotonic() - started) * 1000)
        agent = _agent_id()
        print(f"[LightCall] fell back to the agent's model: {reason} ({detail[:200]}) agent={agent} after {ms} ms")
        trace = current_turn_trace()
        if trace is not None:
            trace.event("light_fallback", agent=agent, reason=reason, ms=ms)
        return LightCallFailed(f"{reason}: {detail}")

    def _broke_off(self, error: BaseException) -> None:
        """Logs a light call that failed after its words began: it hands over with what was said."""
        agent = _agent_id()
        print(f"[LightCall] broke off after its first words: {type(error).__name__}: {str(error)[:200]} "
              f"agent={agent}; handing over")
        trace = current_turn_trace()
        if trace is not None:
            trace.event("light_cut", agent=agent, reason=_why(error))

    async def _astream(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Any = None,
                       **kwargs: Any):
        started = time.monotonic()
        stream = super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs)
        opener = OpenerFilter()
        carried: Optional[AIMessageChunk] = None  # the usage of chunks with nothing to say yet
        try:
            try:
                gen = await asyncio.wait_for(stream.__anext__(), DEADLINE_S)
            except StopAsyncIteration:
                gen = None
            except asyncio.TimeoutError:
                raise self._fell_back(DEADLINE, f"no first chunk after {DEADLINE_S:g} s", started) from None
            except Exception as e:
                raise self._fell_back(_why(e), f"{type(e).__name__}: {e}", started) from e
            while gen is not None:
                text = opener.feed(_text(gen.message.content))
                if opener.said:
                    out = _with_text(gen, text)
                    if carried is not None:
                        out = ChatGenerationChunk(message=carried + out.message, generation_info=out.generation_info)
                        carried = None
                    yield out
                else:
                    empty = gen.message.model_copy(update={"content": ""})
                    carried = empty if carried is None else carried + empty
                if opener.done:
                    break
                try:
                    gen = await stream.__anext__()
                except StopAsyncIteration:
                    break
                except Exception as e:
                    if not opener.said:
                        raise self._fell_back(_why(e), f"{type(e).__name__}: {e}", started) from e
                    self._broke_off(e)
                    break
        finally:
            await stream.aclose()  # the rest of the reply is never used
        if not opener.said:
            raise self._fell_back(NO_SPEECH, "it ended without words in a <speech> block", started)
        closing = opener.close()
        if closing:
            yield ChatGenerationChunk(message=AIMessageChunk(content=closing))
        yield _handover_chunk()

    def _first_line(self, result: ChatResult, started: float) -> ChatResult:
        """A whole reply cut to its first <speech> block, with the `handover` call after it."""
        gen = result.generations[0]
        opener = OpenerFilter()
        text = opener.feed(_text(gen.message.content)) + opener.close()
        if not opener.said:
            raise self._fell_back(NO_SPEECH, "it ended without words in a <speech> block", started)
        call = tool_call(name=HANDOVER, args={}, id=_call_id())
        message = gen.message.model_copy(update={"content": text, "tool_calls": [call]})
        return ChatResult(generations=[ChatGeneration(message=message, generation_info=gen.generation_info)],
                          llm_output=result.llm_output)

    async def _agenerate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Any = None,
                         **kwargs: Any) -> ChatResult:
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs), DEADLINE_S)
        except asyncio.TimeoutError:
            raise self._fell_back(DEADLINE, f"no reply after {DEADLINE_S:g} s", started) from None
        except Exception as e:
            raise self._fell_back(_why(e), f"{type(e).__name__}: {e}", started) from e
        return self._first_line(result, started)

    def _generate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Any = None,
                  **kwargs: Any) -> ChatResult:
        started = time.monotonic()
        try:
            result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        except Exception as e:
            raise self._fell_back(_why(e), f"{type(e).__name__}: {e}", started) from e
        return self._first_line(result, started)
