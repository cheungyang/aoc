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


def split_message(text: str, limit: int = 2000) -> List[str]:
    """Splits a text into chunks of at most 'limit' characters."""
    if not text:
        return []

    chunks = []
    while len(text) > limit:
        # Find the last newline before the limit
        idx = text.rfind('\n', 0, limit)
        if idx == -1:
            # If no newline, find the last space
            idx = text.rfind(' ', 0, limit)
            if idx == -1:
                # If no space, hard split
                idx = limit

        chunks.append(text[:idx].strip())
        text = text[idx:].lstrip()

    if text:
        chunks.append(text)
    return chunks


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
