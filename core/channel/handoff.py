"""A hand-off, as the channels show it (graphs/main's HANDOFF stream event).

The channel the message came from keeps one line, the pointer: "→ 🔬 Ted has
this in #topic-research". The named agent's channel gets what was asked, then
the agent's reply. The Discord runner renders typed turns this way and the
voice room spoken ones, so both read the same.
"""
from typing import Any, Optional

# How much of the request the named agent's channel shows: Discord caps a
# message at 2,000 characters, and the agent has the whole of it anyway.
REQUEST_CHARS = 1500


def pointer_line(event: dict, target: Any) -> str:
    """The pointer, with the channel's name a link to `target` where it has one."""
    pointer = event.get("pointer") or ""
    name, mention = event.get("channel"), getattr(target, "mention", None)
    if name and isinstance(mention, str):
        return pointer.replace(f"#{name}", mention)
    return pointer


def request_line(author: str, source: Any, event: dict, spoken: bool = False) -> str:
    """What was asked, and where, for the top of the named agent's channel."""
    where = getattr(source, "mention", None)
    if not isinstance(where, str):
        where = f"#{getattr(source, 'name', source)}"
    request = " ".join(str(event.get("request") or "").split())
    if len(request) > REQUEST_CHARS:
        request = request[:REQUEST_CHARS].rstrip() + " [...]"
    said = "🎤 " if spoken else ""
    return f"↪️ {said}**{author}** in {where}: {request}".rstrip()


def channel_named(guild: Any, name: Optional[str]) -> Any:
    """`guild`'s text channel called `name`, or None."""
    wanted = str(name or "").lstrip("#").lower()
    if not wanted:
        return None
    for channel in getattr(guild, "text_channels", None) or []:
        if str(getattr(channel, "name", "")).lower() == wanted:
            return channel
    return None
