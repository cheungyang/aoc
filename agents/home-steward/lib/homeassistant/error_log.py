"""Condenses Home Assistant's error log into something an agent can read.

`/api/error_log` returns the whole of `home-assistant.log`, unbounded. One
unreachable device polled every few seconds writes the same traceback tens of
thousands of times a day, and a single `error_log` call was measured returning
150 MB -- ~37M tokens -- which was then stored in the session checkpoint and
re-sent on every later turn.

What an agent needs from a log is *which distinct problems exist, how often,
and how recently*. So entries are grouped by signature, counted, and the most
recently seen groups are shown once each, newest first, within a character
budget.
"""
import re
from typing import Dict, List, Optional, Tuple

DEFAULT_LIMIT = 30          # distinct entries shown
DEFAULT_MAX_CHARS = 20000   # total rendered size
HARD_MAX_CHARS = 100000     # ceiling on what an agent may request
ENTRY_MAX_CHARS = 1500      # one entry's text, traceback included

# `2026-09-21 21:21:08.584 WARNING (SyncWorker_3) [homeassistant.x] message`
_ENTRY_START = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?", re.MULTILINE)
# Values that differ between otherwise identical entries.
_VOLATILE = re.compile(
    r"0x[0-9a-fA-F]+"                     # object addresses
    r"|\b\d+(?:\.\d+)?\s*(?:ms|s|seconds)\b"  # durations
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"  # uuids
)


def _split_entries(text: str) -> List[Tuple[str, str]]:
    """Returns (timestamp, entry_text) pairs. Text before the first timestamp
    (rare: a truncated head) is kept as one untimed entry."""
    starts = [m.start() for m in _ENTRY_START.finditer(text)]
    entries = []
    if not starts:
        return [("", text)] if text.strip() else []
    if starts[0] > 0 and text[:starts[0]].strip():
        entries.append(("", text[:starts[0]]))
    starts.append(len(text))
    for a, b in zip(starts, starts[1:]):
        chunk = text[a:b].rstrip("\n")
        m = _ENTRY_START.match(chunk)
        entries.append((m.group(0) if m else "", chunk))
    return entries


def _signature(entry: str, timestamp: str) -> str:
    """First line (minus timestamp) plus last line: the logger, the message and
    the final exception. Tracebacks in between vary in noise, not in meaning."""
    body = entry[len(timestamp):] if timestamp else entry
    lines = [ln for ln in body.strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    sig = lines[0].strip()[:300]
    if len(lines) > 1:
        sig += " || " + lines[-1].strip()[:300]
    return _VOLATILE.sub("#", sig)


def condense(text: str, limit: Optional[int] = None, max_chars: Optional[int] = None) -> str:
    """Deduplicated, newest-first view of an HA error log, bounded in size."""
    if not isinstance(text, str):
        return text
    limit = max(1, int(limit or DEFAULT_LIMIT))
    max_chars = min(max(1000, int(max_chars or DEFAULT_MAX_CHARS)), HARD_MAX_CHARS)

    if len(text) <= max_chars and text.count("\n") < limit * 5:
        return text  # small enough to pass through verbatim

    entries = _split_entries(text)
    groups: Dict[str, Dict] = {}
    for order, (ts, entry) in enumerate(entries):
        sig = _signature(entry, ts)
        g = groups.get(sig)
        if g is None:
            groups[sig] = {"count": 1, "first": ts, "last": ts, "order": order, "text": entry}
        else:
            g["count"] += 1
            g["last"] = ts or g["last"]
            g["order"] = order
            g["text"] = entry  # keep the most recent occurrence

    ranked = sorted(groups.values(), key=lambda g: g["order"], reverse=True)
    total_entries = len(entries)
    header = (
        f"[Condensed Home Assistant error log: {total_entries} entries, "
        f"{len(groups)} distinct, {len(text)} chars raw. Newest first; repeats "
        f"collapsed. Pass 'limit' / 'max_chars' (max {HARD_MAX_CHARS}) to see more.]"
    )

    parts = [header]
    used = len(header)
    shown = 0
    for g in ranked[:limit]:
        body = g["text"]
        if len(body) > ENTRY_MAX_CHARS:
            # Keep both ends: the first line names the logger and message, the
            # last line is the exception that actually explains it.
            head = ENTRY_MAX_CHARS * 3 // 5
            body = body[:head] + "\n    ... [traceback truncated] ...\n" + body[-(ENTRY_MAX_CHARS - head):]
        if g["count"] > 1:
            tag = f"[x{g['count']}, first {g['first'] or '?'}, last {g['last'] or '?'}]"
        else:
            tag = "[x1]"
        block = f"\n\n{tag}\n{body}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
        shown += 1

    if shown < len(groups):
        parts.append(f"\n\n[{len(groups) - shown} older distinct entries omitted.]")
    return "".join(parts)
