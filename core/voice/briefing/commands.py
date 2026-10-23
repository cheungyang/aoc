"""`!inbox`: what the channel inbox holds, so a misjudged row can be spotted and tuned.

    !inbox                 every row, newest first: channel, agent, status,
                           priority, source, label, age, and its question
    !inbox done            marks this channel's row done
    !inbox done <channel>  marks that channel's row done
"""
import time
from typing import List, Optional

from core.storage.channel_inbox import InboxStore
from core.storage.channel_inbox import UNFINISHED, InboxRow, Status, Via

USAGE = "Usage: `!inbox` lists the inbox; `!inbox done [channel]` marks a channel's row done."


def _age(seconds: float) -> str:
    minutes = int(max(seconds, 0) // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    return f"{hours}h ago" if hours < 48 else f"{hours // 24}d ago"


def format_row(row: InboxRow, now: float) -> str:
    parts = [f"`#{row.channel}`", row.agent_id, f"**{row.status.value}**", row.priority.value, row.source.value]
    if row.label:
        parts.append(f"\u201c{row.label}\u201d")
    parts.append(_age(now - row.posted_at))
    if row.speech is None and row.status in UNFINISHED:
        parts.append("speech not written yet")
    line = "\u2022 " + " \u00b7 ".join(parts)
    if row.question:
        line += f"\n  \u2753 {row.question}"
    return line


def format_inbox(rows: List[InboxRow], now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    if not rows:
        return "\U0001f4ed The inbox is empty."
    waiting = sum(1 for r in rows if r.status in UNFINISHED)
    header = f"\U0001f4ec **Inbox**: {waiting} unfinished of {len(rows)}"
    return "\n".join([header] + [format_row(r, now) for r in rows])


def handle_inbox_command(args: Optional[str], here: Optional[str], store: Optional[InboxStore] = None,
                         now: Optional[float] = None) -> str:
    """The reply to `!inbox {args}` typed in channel `here`."""
    store = store or InboxStore()
    words = (args or "").split()
    if not words:
        return format_inbox(store.rows(now), now)
    if words[0].lower() == "done" and len(words) <= 2:
        channel = (words[1] if len(words) == 2 else here or "").lstrip("#").strip().lower()
        if not channel:
            return USAGE
        if store.set_status(channel, Status.DONE, Via.TYPED, now=now):
            return f"\u2705 #{channel} is done."
        return f"Nothing unfinished in #{channel}."
    return USAGE
