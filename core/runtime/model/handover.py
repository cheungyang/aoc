"""The light first call's hand-over marker (graphs/voice).

A voice turn's first model call can run on a light model, with no tools: it
speaks first, then either answers or ends its reply with
`<handover depth="low"/>` (minimal, low or medium) for the agent's own model
to take over, with its tools (graphs/voice/prompts/light_call.md).

`LightChatModel` takes the marker out as the reply streams, as the model
wrapper does `<next_thinking>` (core/runtime/model/thinking.py), so nothing
hears or shows it, and puts a `handover` tool call on the reply in its place.
FLASH and FLASH_LITE refuse a request that ends on the model's own turn, so
the agent's model has to start after a function response: graphs/voice runs
the call locally (its `handover` tool), and no model ever has it declared.

A reply that's only its opener -- one <speech> block with nothing after it,
and no marker -- hands over too, at the default depth, its call saying
`marker="missing"` (`opener_only`): the light model said what it would do and
left the marker off. An answer has its text reply after </speech>, as
light_call.md asks, so it never looks like one.
"""
import json
import re
import uuid
from typing import Any, ClassVar, Dict, List, Optional

from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.messages.tool import tool_call, tool_call_chunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from core.config.models import THINKING_BY_SURFACE
from core.runtime.model.retry import RetryingChatGoogleGenerativeAI, _map_text, _without_marker
from core.runtime.model.thinking import TagFilter

HANDOVER = "handover"  # the tool call the marker becomes

# The depth a marker without one asks for: the voice default.
DEFAULT_DEPTH = THINKING_BY_SURFACE["voice"]

# What a hand-over's call says when the light call left the marker off.
MISSING = "missing"

_OPEN = "<handover"
_TAG = re.compile(r"""<handover(?:\s+depth\s*=\s*["']?(\w{0,20})["']?)?\s*/?>[ \t]*\n?""", re.IGNORECASE)
_LONGEST = 48  # `<handover depth="minimal" />` with room to spare; longer still open isn't one

# One <speech> block, and nothing but whitespace around it.
_OPENER_ONLY = re.compile(r"\s*<speech>(?:(?!</speech>).)*</speech>\s*", re.DOTALL | re.IGNORECASE)


class HandoverFilter(TagFilter):
    """Takes the marker out of a reply's text as it streams; `depth` is what it
    asked for (None without a marker, DEFAULT_DEPTH for one that names none)."""

    def __init__(self):
        super().__init__(_TAG, _OPEN, ">", _LONGEST)

    @property
    def depth(self) -> Optional[str]:
        return (self.value or DEFAULT_DEPTH) if self.found else None


def _call_id() -> str:
    return f"{HANDOVER}-{uuid.uuid4().hex[:12]}"


def take_handover(text: str):
    """`text` without its markers, and the depth the last one asked for (None without one)."""
    depths: List[str] = []

    def drop(match):
        depths.append((match.group(1) or "").strip().lower() or DEFAULT_DEPTH)
        return ""

    return _TAG.sub(drop, text), (depths[-1] if depths else None)


def opener_only(text: str) -> bool:
    """Whether a light reply is only its opener: one <speech> block with
    nothing after it, where an answer has its text reply too."""
    return bool(_OPENER_ONLY.fullmatch(text or ""))


def _text(content: Any) -> str:
    """The text parts of a message's content, joined."""
    if isinstance(content, str):
        return content
    return "".join(block if isinstance(block, str) else block.get("text", "") for block in content
                   if isinstance(block, str) or (isinstance(block, dict) and block.get("type") == "text"))


def _handover_args(depth: Optional[str], text: str) -> Optional[Dict[str, str]]:
    """The `handover` call's arguments for a light reply whose text is `text`:
    the marker's `depth`; the default depth, marked missing, for an opener
    without one; None for an answer, which ends the turn."""
    if depth is not None:
        return {"depth": depth}
    if opener_only(text):
        return {"depth": DEFAULT_DEPTH, "marker": MISSING}
    return None


def _handed_over(result: ChatResult) -> ChatResult:
    """A whole reply without its marker, with the `handover` call in its place
    (or after a lone opener); the same result when it's an answer."""
    generations, changed = [], False
    for gen in result.generations:
        depths: List[str] = []

        def take(text: str) -> str:
            text, depth = take_handover(text)
            if depth:
                depths.append(depth)
            return text

        content = _map_text(gen.message.content, take)
        args = _handover_args(depths[-1] if depths else None, _text(content))
        if args is None:
            generations.append(gen)
            continue
        changed = True
        calls = [*getattr(gen.message, "tool_calls", []), tool_call(name=HANDOVER, args=args, id=_call_id())]
        message = gen.message.model_copy(update={"content": content, "tool_calls": calls})
        generations.append(ChatGeneration(message=message, generation_info=gen.generation_info))
    return ChatResult(generations=generations, llm_output=result.llm_output) if changed else result


class LightChatModel(RetryingChatGoogleGenerativeAI):
    """The light first call's model: a reply's `<handover depth="…"/>` marker
    becomes a `handover` tool call, and so does a lone opener. Its calls say
    `light` on the voice trace."""

    trace_fields: ClassVar[Dict[str, Any]] = {"light": True}

    async def _astream(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Any = None,
                       **kwargs: Any):
        marker, said = HandoverFilter(), []
        async for gen in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            gen = _without_marker(gen, marker)
            said.append(_text(gen.message.content))
            yield gen
        held = marker.flush()
        if held:
            said.append(held)
            yield ChatGenerationChunk(message=AIMessageChunk(content=held))
        args = _handover_args(marker.depth, "".join(said))
        if args is not None:
            # Text-free: the call is the reply's last part, in the stream as in history.
            call = tool_call_chunk(name=HANDOVER, args=json.dumps(args), id=_call_id(), index=0)
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[call]))

    async def _agenerate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Any = None,
                         **kwargs: Any) -> ChatResult:
        return _handed_over(await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs))

    def _generate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, run_manager: Any = None,
                  **kwargs: Any) -> ChatResult:
        return _handed_over(super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs))
