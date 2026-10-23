"""Writing the channel inbox: pushes, replies and read signals.

Nothing here raises. As with `record_channel_post`, losing an inbox write is
better than failing the run, reply or reaction that caused it.

    record_push    a scheduled run's reply, a script's output or a scan's problem
                   notice puts its channel in the inbox, unless its priority is IGNORE
    record_reply   an agent's reply to a typed message: a candidate, judged by
                   the speech writer only once you join voice; or a late reply
    mark_read      you posted in the channel, typed or spoken, or ✅'d a post there
    mark_heard     the voice played a row: AWAITING_REPLY if it asks you
                   something, else DONE
    store_speech   the speech writer's result for a row

A row's agent is whoever answers an untagged message in its channel: the
voice focus target of Part 1.
"""
import asyncio
import sys
import time
from typing import Any, Callable, List, Optional, Tuple

from core.voice.briefing.model import (
    LATE_REPLY_S,
    InboxRow,
    Priority,
    Source,
    Status,
    Via,
    excerpt_of,
)
from core.storage.channel_inbox import InboxStore

# A ✅ counts for a row if the message it's on is no older than this before
# the row's post: a run posts while it works, and its row is written at the end.
REACTION_WINDOW_S = 15 * 60

# How the digest and announcements name a row without a schedule label.
REPLY_LABEL = "a question for you"
LATE_REPLY_LABEL = "a reply for you"
DEFAULT_LABEL = "an update"

Listener = Callable[[InboxRow], None]
_listeners: List[Tuple[Listener, Optional[asyncio.AbstractEventLoop]]] = []


def _log(message: str) -> None:
    print(f"[inbox] {message}", file=sys.stderr)


# --- listeners: the voice hears about new rows while you're in it -------------------

def subscribe(listener: Listener) -> Callable[[], None]:
    """Calls `listener(row)` after each new row, on the event loop running now
    (the subscriber's). Returns the function that unsubscribes."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    entry = (listener, loop)
    _listeners.append(entry)

    def unsubscribe() -> None:
        if entry in _listeners:
            _listeners.remove(entry)
    return unsubscribe


def _notify(row: InboxRow) -> None:
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        current = None
    for listener, loop in list(_listeners):
        try:
            if loop is None or loop is current:
                listener(row)
            elif not loop.is_closed():
                loop.call_soon_threadsafe(listener, row)
        except Exception as e:
            _log(f"A listener failed on #{row.channel}: {e}")


# --- whose row ----------------------------------------------------------------------

def channel_name(channel: Any) -> Optional[str]:
    """The inbox key for a channel object or name. None for a thread (out of
    scope, as in Part 1) or for nothing."""
    if channel is None:
        return None
    if not isinstance(channel, str):
        from core.runtime.foundation.context import _is_thread
        if _is_thread(channel):
            return None
    from core.runtime.history.channel_posts import _channel_name
    name = _channel_name(channel).strip().lower()
    return name or None


def answering_agent_for(name: str, loader: Any = None, fallback: Optional[str] = None) -> Optional[str]:
    """Who answers an untagged message in channel `name`: routed as the voice
    host routes it, else as its first host does. `fallback` when nobody hosts it."""
    from core.loaders.agents_loader import AgentsLoader
    from core.runtime.history.channel_posts import channel_hosts_for
    from core.voice.room.focus import answering_agent

    loader = loader or AgentsLoader()
    hosts = channel_hosts_for(name, loader)
    if not hosts:
        return fallback
    host = next((h for h in hosts if _voice_enabled(loader, h)), hosts[0])
    return answering_agent(host, name, loader)


def _voice_enabled(loader: Any, agent_id: str) -> bool:
    voice = (loader.get_agent_config(agent_id) or {}).get("voice_config") or {}
    return isinstance(voice, dict) and bool(voice.get("enabled"))


# --- posts --------------------------------------------------------------------------

def record_push(channel: Any, text: str, *, schedule_id: str, agent_id: str,
                priority: Any = Priority.DEFAULT, label: Optional[str] = None,
                speech: Optional[str] = None, question: Optional[str] = None,
                loader: Any = None, now: Optional[float] = None) -> Optional[InboxRow]:
    """A scheduled post puts its channel in the inbox, replacing whatever row was there.

    `speech` is the speech version the run wrote, with its marked `question`;
    without one (a script's output) it's written once you join voice.
    """
    try:
        priority = Priority.parse(priority)
        name = channel_name(channel)
        if priority is Priority.IGNORE or not name or not (text or "").strip():
            return None
        row = InboxRow(
            channel=name,
            agent_id=answering_agent_for(name, loader, fallback=agent_id) or agent_id,
            posted_at=time.time() if now is None else now,
            source=Source.SCHEDULE,
            priority=priority,
            schedule_id=schedule_id,
            label=(label or "").strip() or DEFAULT_LABEL,
            speech=(speech or "").strip() or None,
            question=((question or "").strip() or None) if (speech or "").strip() else None,
            excerpt=excerpt_of(text),
        )
        InboxStore().put(row)
    except Exception as e:
        _log(f"Could not record the push from '{schedule_id}': {e}")
        return None
    _notify(row)
    return row


def record_reply(channel: Any, text: str, *, asked_at: Optional[float], agent_id: Optional[str] = None,
                 loader: Any = None, now: Optional[float] = None) -> Optional[InboxRow]:
    """An agent answered a typed message in `channel`.

    A late reply (10+ minutes after your message, `asked_at`) is a row at once.
    Any other reply is a candidate: the speech writer judges it when you join
    voice and keeps it only if it asks you something. Posting in the channel
    before then drops it (`mark_read`). Voice turns never come here: you heard them.
    """
    try:
        name = channel_name(channel)
        if not name or not (text or "").strip():
            return None
        now = time.time() if now is None else now
        late = asked_at is not None and now - asked_at >= LATE_REPLY_S
        row = InboxRow(
            channel=name,
            agent_id=answering_agent_for(name, loader, fallback=agent_id) or agent_id or "",
            posted_at=now,
            source=Source.LATE_REPLY if late else Source.REPLY,
            label=LATE_REPLY_LABEL if late else REPLY_LABEL,
            excerpt=excerpt_of(text),
        )
        InboxStore().put(row)
    except Exception as e:
        _log(f"Could not record a reply in #{channel_name(channel) if channel else '?'}: {e}")
        return None
    _notify(row)
    return row


# --- read signals -------------------------------------------------------------------

def mark_read(channel: Any, via: Via, *, message_time: Optional[float] = None) -> bool:
    """You finished with the channel's row: you posted there (typed or spoken),
    or ✅'d a post there. A candidate there is dropped the same way.

    For a reaction, `message_time` is when the ✅'d message was posted: a
    message much older than the row's post is about an earlier post, and
    changes nothing.
    """
    try:
        name = channel_name(channel)
        if not name:
            return False
        posted_before = message_time + REACTION_WINDOW_S if message_time is not None else None
        return InboxStore().set_status(name, Status.DONE, via, posted_before=posted_before)
    except Exception as e:
        _log(f"Could not mark #{channel} read: {e}")
        return False


def mark_heard(row: InboxRow) -> Optional[InboxRow]:
    """The voice just played `row`'s speech version: it now waits for your
    answer if it asks you something, and is finished if not."""
    status = Status.AWAITING_REPLY if row.question else Status.DONE
    try:
        if row.status is status:
            return row
        if InboxStore().set_status(row.channel, status, Via.VOICE, posted_at=row.posted_at):
            return row.with_status(status, Via.VOICE)
    except Exception as e:
        _log(f"Could not mark #{row.channel} heard: {e}")
    return None


def store_speech(row: InboxRow, speech: str, question: Optional[str]) -> Optional[InboxRow]:
    """The speech writer's result for `row`. A candidate that asks nothing is
    dropped (DONE); everything else keeps its status. None if the row was
    replaced or finished meanwhile."""
    from dataclasses import replace

    speech = (speech or "").strip()
    question = (question or "").strip() or None
    if not speech:
        return None
    drop = row.is_candidate and not question
    try:
        current = InboxStore().get(row.channel)
        if current is None or current.posted_at != row.posted_at:
            return None
        status = Status.DONE if drop else None
        if not InboxStore().set_speech(row.channel, row.posted_at, speech, question, status=status):
            return None
        return replace(current, speech=speech, question=question,
                       status=Status.DONE if drop else current.status)
    except Exception as e:
        _log(f"Could not store the speech version for #{row.channel}: {e}")
        return None
