"""What a channel is called, and whether it's a thread.

Channels arrive as Discord objects, as names, or as test doubles. Sessions,
channel posts, the channel inbox and the voice focus all key on a channel's
name, with a thread counting as its parent channel: these two functions are
the one place that knows how to tell.
"""
import sys
from typing import Any


def is_thread(channel: Any) -> bool:
    """Checks whether a channel object represents a thread without hard import."""
    if channel is None or isinstance(channel, str):
        return False
    if getattr(channel, "is_thread", False) is True:
        return True
    discord_mod = sys.modules.get("discord")
    if discord_mod:
        thread_cls = getattr(discord_mod, "Thread", None)
        if thread_cls and isinstance(channel, thread_cls):
            return True
        text_cls = getattr(discord_mod, "TextChannel", None)
        if text_cls and isinstance(channel, text_cls):
            return False
    spec = getattr(channel, "_spec_class", None)
    if spec and "Thread" in getattr(spec, "__name__", ""):
        return True
    cls_name = getattr(type(channel), "__name__", "")
    if "Thread" in cls_name:
        return True
    return False


def channel_name(channel: Any) -> str:
    """The name sessions key on: a thread reports its parent channel's name."""
    if channel is None:
        return ""
    if isinstance(channel, str):
        return channel.lstrip("#")
    if is_thread(channel):
        parent = getattr(channel, "parent", None)
        if parent is not None and getattr(parent, "name", None):
            return str(parent.name).lstrip("#")
    return str(getattr(channel, "name", "") or "").lstrip("#")
