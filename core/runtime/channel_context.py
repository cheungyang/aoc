"""Making out-of-band channel posts visible to whoever answers the next reply.

A scheduled run executes in its own session (`<agent>:scheduled:<channel>`) and
posts its result to Discord. The bot ignores its own messages, so nothing else
ever learns what was posted. The user then replies in the channel, and the turn
is answered from a *different* session that has never seen the post.

Which session that is depends on routing, not on who made the post:

  - In a channel with exactly one eligible specialist, the host (`main`) routes
    deterministically (`graphs.main.router.decide`) and the specialist answers
    from its delegation session, `<specialist>:tool:<channel>[:<thread>]`.
    The host's own discord session is never read by a model on that path.
  - Otherwise the host answers through its concierge, from its own
    `<host>:discord:<channel>[:<thread>]` session.

`record_channel_post` asks the router the same question the next untagged
message will ask, then appends the post to that session as an attributed
assistant message. The scheduled session itself is left alone.

Known limitation: the append is a read-modify-write of the latest checkpoint.
If a turn on the same session is mid-flight, its next checkpoint supersedes
this one and the note is lost (not corrupted). Scheduled posts rarely land in
the middle of an active exchange in the same session, so this is accepted.
"""
import sys
from typing import Any, List, Optional

from langchain_core.messages import AIMessage

# A script can dump a lot of output; the channel already shows all of it, so
# the session only needs enough to know what the post was about.
MAX_RECORDED_CHARS = 8000


def channel_hosts_for(channel_name: str, loader: Any) -> List[str]:
    """Agent ids whose `channel_hosts` include `channel_name`, in loader order."""
    target = str(channel_name or "").lstrip("#").lower()
    if not target:
        return []
    hosts = []
    for agent_id in loader.list_agent_ids():
        config = loader.get_agent_config(agent_id) or {}
        if target in [str(h).lower() for h in (config.get("channel_hosts") or [])]:
            hosts.append(agent_id)
    return hosts


def reply_sessions(channel: Any, loader: Any = None) -> list:
    """The sessions that would answer an untagged reply in `channel`.

    `channel` is a Discord channel/thread object or a channel name. One session
    per host, deduplicated; a channel nobody hosts has no reply path.
    """
    from core.loaders.agents_loader import AgentsLoader
    from core.runtime.session_manager import SessionManager
    from graphs.main.router import decide

    loader = loader or AgentsLoader()
    sessions = {}

    for host_id in channel_hosts_for(_channel_name(channel), loader):
        host_config = loader.get_agent_config(host_id) or {}
        host_session = SessionManager.get_session(host_id, source="discord", channel=channel)

        decision = decide(
            prompt="",
            agent_id=host_id,
            channel_name=host_session.channel_name,
            agent_config=host_config,
            loader=loader,
        )
        target = host_session
        if decision.is_direct and decision.agent_id:
            target_config = loader.get_agent_config(decision.agent_id) or {}
            if target_config.get("stateless"):
                # A stateless specialist keeps no history, so there is nothing
                # to make it aware of.
                continue
            target = SessionManager.get_session(decision.agent_id, source="tool", channel=channel)
        sessions.setdefault(target.session_id, target)

    return list(sessions.values())


def _channel_name(channel: Any) -> str:
    """The name sessions key on: a thread reports its parent channel's name."""
    if channel is None:
        return ""
    if isinstance(channel, str):
        return channel.lstrip("#")
    from core.runtime.execution_context import _is_thread
    if _is_thread(channel):
        parent = getattr(channel, "parent", None)
        if parent is not None and getattr(parent, "name", None):
            return str(parent.name).lstrip("#")
    return str(getattr(channel, "name", "") or "").lstrip("#")


def format_post(text: str, origin: str, posted_at: Optional[Any] = None) -> str:
    """The attributed body stored in the session."""
    from core.util.time_util import get_local_now

    posted_at = posted_at or get_local_now()
    body = text.strip()
    if len(body) > MAX_RECORDED_CHARS:
        body = body[:MAX_RECORDED_CHARS].rstrip() + "\n[... truncated; full text is in the channel]"
    stamp = posted_at.strftime("%Y-%m-%d %H:%M %Z").strip()
    return f"[Posted to this channel by scheduled job '{origin}' at {stamp}]\n{body}"


def append_to_session(session: Any, message: Any, checkpointer: Any = None) -> bool:
    """Appends `message` to the latest checkpoint of `session`'s thread.

    Mirrors how `ContextPruner` rewrites a checkpoint, but under a fresh
    checkpoint id so the stale snapshot (and any pending writes attached to it)
    is not what the next turn resumes from. Returns False if it declined.
    """
    from langgraph.checkpoint.base import copy_checkpoint, empty_checkpoint
    from langgraph.checkpoint.base.id import uuid6
    from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer

    checkpointer = checkpointer or SqliteCheckpointer()
    thread_id = session.get_session_thread_id()
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    existing = checkpointer.get_tuple(config)

    if existing and existing.checkpoint:
        # `get_tuple` has already closed any dangling tool calls with synthetic
        # ToolMessages, so appending after them leaves a valid sequence.
        checkpoint = copy_checkpoint(existing.checkpoint)
        values = dict(checkpoint.get("channel_values") or {})
        messages = list(values.get("messages") or [])
        values["messages"] = messages + [message]
        checkpoint["channel_values"] = values
        checkpoint["id"] = str(uuid6(clock_seq=-2))
        checkpointer.put(existing.config, checkpoint, existing.metadata or {}, new_versions={})
        return True

    checkpoint = empty_checkpoint()
    version = checkpointer.get_next_version(None, None)
    checkpoint["channel_values"] = {"messages": [message]}
    checkpoint["channel_versions"] = {"messages": version}
    metadata = {"source": "update", "step": -1, "parents": {}}
    checkpointer.put(config, checkpoint, metadata, new_versions={"messages": version})
    return True


def record_channel_post(channel: Any, text: str, origin: str, loader: Any = None) -> List[str]:
    """Makes a post visible to every session that answers replies in `channel`.

    Never raises: losing the note is better than failing the run that posted.
    Returns the session ids written to.
    """
    if channel is None or not (text or "").strip():
        return []

    written = []
    try:
        targets = reply_sessions(channel, loader=loader)
    except Exception as e:
        print(f"[channel_context] Could not resolve reply sessions for '{origin}': {e}", file=sys.stderr)
        return []

    body = format_post(text, origin)
    for session in targets:
        try:
            if append_to_session(session, AIMessage(content=body)):
                written.append(session.session_id)
                _log_transcript(session.session_id, body)
        except Exception as e:
            print(f"[channel_context] Could not record '{origin}' into {session.session_id}: {e}", file=sys.stderr)

    if written:
        print(f"[channel_context] Recorded post from '{origin}' into: {', '.join(written)}")
    return written


def _log_transcript(session_id: str, body: str) -> None:
    """Keeps the human-readable log consistent with the checkpoint (see
    `graphs.main.graph._record_turn` for why this is separate)."""
    try:
        from core.knowledge.memory.sqlite_session_store import SqliteSessionStore
        SqliteSessionStore().append_message(session_id, "ai", body)
    except Exception as e:
        print(f"[channel_context] Warning: could not log transcript for {session_id}: {e}", file=sys.stderr)
