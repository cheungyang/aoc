"""Splitting text into Discord-sized messages, code fences intact.

Discord caps a message at 2,000 characters and pairs markdown across the whole
message, so a cut in the wrong place leaves a code block open for the rest of
the conversation. `safe_chunks` is the one splitter every sender uses: the
Discord dispatcher and stream buffer, command replies
(core/runtime/commands/base.py) and the scheduler.
"""
import re
from typing import List

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
