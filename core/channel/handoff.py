"""A hand-off, as the channels show it (graphs/main's HANDOFF stream event).

The channel the message came from keeps one line, the pointer: "→ 🔬 Ted has
this in #topic-research". The named agent's channel gets what was asked, then
the agent's reply. The Discord runner renders typed turns this way and the
voice room spoken ones, so both read the same.

An agent can hand you over too, when you ask it for a teammate: its reply ends
with <switch to="goal-setter"/> (core/prompts/templates/system/team.md). The
channel then shows where to go -- "→ Over to 🚀 Grace in #project-planning" --
and the voice room moves there.
"""
from typing import Any, Optional

from core.loaders.agents_loader import AgentsLoader
from core.runtime.foundation.routing import home_channel

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


def switch_pointer(agent: str, source: Any, loader: Any = None) -> Optional[str]:
    """What a channel shows when its agent hands you to a teammate:
    "→ Over to 🚀 Grace in #project-planning", the channel a link where
    `source`'s guild has it. `agent` is the teammate's id, or its name. None
    when no agent by that id or name has a channel to go to."""
    loader = loader if loader is not None else AgentsLoader()
    agent_id = _agent_named(agent, loader)
    home = home_channel(agent_id, loader) if agent_id else None
    if not home:
        return None
    config = loader.get_agent_config(agent_id) or {}
    mention = getattr(channel_named(getattr(source, "guild", None), home), "mention", None)
    where = mention if isinstance(mention, str) else f"#{home}"
    return f"→ Over to {config.get('emoji', '🤖')} {config.get('name', agent_id)} in {where}"


def _agent_named(agent: str, loader: Any) -> Optional[str]:
    """The id of the agent whose id or name `agent` is, or None."""
    wanted = str(agent or "").strip()
    if not wanted:
        return None
    if loader.get_agent_config(wanted):
        return wanted
    for agent_id in loader.list_agent_ids():
        name = (loader.get_agent_config(agent_id) or {}).get("name")
        if isinstance(name, str) and name.casefold() == wanted.casefold():
            return agent_id
    return None


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
