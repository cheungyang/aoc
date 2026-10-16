import json
import re
from typing import Any, List, Sequence
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)


# Discord ends a code block at the next ``` run, whatever follows it, so the
# splitter toggles its fence state on every run of three or more backticks.
_FENCE = "```"
_FENCE_RUN = re.compile(r"`{3,}")
_FENCE_OPENER = re.compile(r"^\s*`{3,}([\w+#.-]*)\s*$")
_LONE_TICK = re.compile(r"(?<![`\\])`(?!`)")
_MAX_LANG = 32  # a longer language tag is not carried over to a reopened block


def _escape_unmatched_ticks(line: str) -> str:
    """Escapes the last single backtick on a line that has an odd number of them.

    Discord pairs inline-code backticks across lines, so one stray backtick can
    turn the rest of a message into code. Escaping keeps it visible but inert.
    """
    ticks = [m.start() for m in _LONE_TICK.finditer(line)]
    if len(ticks) % 2 == 0:
        return line
    i = ticks[-1]
    return line[:i] + "\\`" + line[i + 1:]


def _split_long_line(line: str, room: int) -> List[str]:
    """Splits a line into pieces of at most `room` chars, at spaces when possible."""
    parts = []
    while len(line) > room:
        cut = line.rfind(" ", 0, room)
        if cut <= 0:
            cut = room
        parts.append(line[:cut])
        line = line[cut:].lstrip(" ")
    parts.append(line)
    return parts


def safe_chunks(text: str, limit: int = 1900, streaming: bool = False) -> List[str]:
    """Splits text into Discord messages of at most `limit` chars without breaking markdown.

    The one splitter for every message sent to Discord. The default limit
    leaves headroom under Discord's 2,000-character cap.

    - Splits at line boundaries, then spaces; hard-splits only a single overlong word.
    - Never leaves a code block open across messages: closes it at the end of a
      chunk and reopens it, with the same language, at the start of the next.
    - Escapes unmatched inline backticks outside code blocks.
    - streaming=True also closes a block whose end hasn't arrived yet, so a live
      preview doesn't swallow the rest of the message.
    """
    if not text:
        return []
    room = limit - len(_FENCE) - 1  # always leave space for a closing fence
    line_room = room - len(_FENCE) - _MAX_LANG - 1  # ...and for a reopening one
    if line_room < 1:
        raise ValueError(f"limit={limit} is too small to split around code fences")

    chunks: List[str] = []
    cur: List[str] = []
    size = 0  # len("\n".join(cur))
    in_fence, lang = False, ""

    def flush(final: bool = False):
        nonlocal cur, size
        close = in_fence and (streaming or not final)
        if close and _FENCE_OPENER.match(cur[-1]):
            # The block has no content yet: leave its opener to the next
            # chunk's reopening fence rather than end on an empty block.
            cur.pop()
            close = False
        body = "\n".join(cur) + ("\n" + _FENCE if close else "")
        chunks.append(body.strip())
        cur = [_FENCE + lang] if in_fence else []
        size = len(cur[0]) if cur else 0

    for raw in text.split("\n"):
        line = raw if (in_fence or _FENCE_RUN.search(raw)) else _escape_unmatched_ticks(raw)
        # Pieces of a split line become separate lines, so fence state is
        # tracked per piece.
        for piece in _split_long_line(line, line_room):
            toggles = len(_FENCE_RUN.findall(piece)) % 2 == 1
            # A piece that closes the block needs no closing fence after it.
            budget = limit if (in_fence and toggles) else room
            if cur and size + 1 + len(piece) > budget:
                flush()
            size += len(piece) + (1 if cur else 0)
            cur.append(piece)
            if toggles:
                opener = None if in_fence else _FENCE_OPENER.match(piece)
                in_fence = not in_fence
                lang = opener.group(1) if opener and len(opener.group(1)) <= _MAX_LANG else ""

    if cur:
        flush(final=True)
    return [c for c in chunks if c]


def estimate_message_tokens(msg: Any) -> int:
    """
    Estimates the token count of a single message or dict.
    Uses standard ~4 characters per token heuristic + overhead for tool calls/metadata.
    """
    if msg is None:
        return 0

    tokens = 4  # Baseline message role/framing overhead

    # Extract text content
    content = getattr(msg, "content", msg if isinstance(msg, (str, dict, list)) else "")
    if isinstance(content, str):
        tokens += max(1, len(content) // 4)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                tokens += max(1, len(item) // 4)
            elif isinstance(item, dict):
                text_val = item.get("text") or item.get("content") or ""
                if text_val:
                    tokens += max(1, len(str(text_val)) // 4)
                # Media/image parts estimate
                if item.get("type") in ("image_url", "image", "media"):
                    tokens += 300
    elif isinstance(content, dict):
        tokens += max(1, len(json.dumps(content)) // 4)

    # Tool calls in AIMessage
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls and isinstance(tool_calls, list):
        for tc in tool_calls:
            tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
            tc_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
            tokens += 10 + max(1, len(str(tc_name)) // 4) + max(1, len(json.dumps(tc_args)) // 4)

    # ToolMessage metadata
    if isinstance(msg, ToolMessage):
        tool_name = getattr(msg, "name", "")
        tc_id = getattr(msg, "tool_call_id", "")
        tokens += 5 + max(1, len(str(tool_name)) // 4) + max(1, len(str(tc_id)) // 4)

    return tokens


def estimate_total_tokens(messages: Sequence[Any]) -> int:
    """Estimates total tokens for a sequence of messages."""
    return sum(estimate_message_tokens(m) for m in messages)


def find_safe_boundary(messages: Sequence[BaseMessage], window_messages: int = 15) -> int:
    """
    Finds a safe split index `split_idx` dividing `messages` into `older = messages[:split_idx]`
    and `recent = messages[split_idx:]`.

    Safety Invariant:
    - Never split an AIMessage(tool_calls=[...]) from its corresponding ToolMessage(s).
    - `recent` MUST begin on a clean `HumanMessage` turn.
    - Preserves at least `window_messages` recent messages when possible.
    """
    total_len = len(messages)
    if total_len <= window_messages:
        return 0

    target_idx = max(0, total_len - window_messages)

    # 1. Check if target_idx is already a HumanMessage
    if target_idx > 0 and isinstance(messages[target_idx], HumanMessage):
        return target_idx

    # 2. Search backwards from target_idx for the closest preceding HumanMessage
    for idx in range(target_idx - 1, 0, -1):
        if isinstance(messages[idx], HumanMessage):
            return idx

    # 3. If none found backwards (excluding index 0), search forward for the next HumanMessage
    for idx in range(target_idx + 1, total_len):
        if isinstance(messages[idx], HumanMessage):
            return idx

    # 4. If no other HumanMessage exists, we cannot safely split without violating turn invariants
    return 0


_INLINE_IMAGE = re.compile(
    r'data:image/[a-zA-Z+]+;base64,[A-Za-z0-9+/=\r\n]+'
    r'|(?<=<instruction_result action="read_image" path=")[^"]*">[A-Za-z0-9+/=\r\n]+'
)


def cap_tool_output(text: Any, max_chars: int) -> Any:
    """Bounds a tool's string result, keeping its head and tail.

    Inline image base64 is not counted: a `read_image` result is large by
    nature, is needed intact for the model to see it, and is evicted from the
    checkpoint once processed. Everything else over `max_chars` is cut in the
    middle -- the head usually says what the output is, and the tail holds the
    closing envelope (`</payload><errors>...`) and the most recent log lines.

    Non-strings (content-and-artifact tuples, ToolMessages, Commands) pass
    through untouched.
    """
    if not isinstance(text, str) or max_chars <= 0 or len(text) <= max_chars:
        return text

    if "data:image/" in text or 'action="read_image"' in text:
        image_chars = sum(len(m.group(0)) for m in _INLINE_IMAGE.finditer(text))
        if len(text) - image_chars <= max_chars:
            return text

    head = int(max_chars * 0.7)
    tail = max_chars - head
    omitted = len(text) - head - tail
    marker = (
        f"\n\n... [tool output truncated: {omitted} of {len(text)} chars omitted "
        f"(~{omitted // 4} tokens). Narrow the request to see the missing part.] ...\n\n"
    )
    return text[:head] + marker + text[-tail:]
