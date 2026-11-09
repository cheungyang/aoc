"""Which channels an agent hosts, read from its `channel_hosts`.

The host of a channel answers its untagged messages (BotRunner), and graphs/main
hands each one to the channel's agent (core/runtime/foundation/routing.py). The
Concierge is the only host, and its `channel_hosts` is `["*"]`: every channel on
the server. A channel with exactly one agent goes to that agent. A channel in
the Concierge's `shared_channels` (#general) gets its fixed line. Any other
channel gets a notice that it isn't set up, so a channel nobody lists, or that
two agents both list, says so instead of going quiet.

Two questions, answered differently for "*":

  - "Does it host #x?" (`hosts`): yes, for any channel with a name. A DM has
    none, so only an entry naming its id hosts it.
  - "Which channels does it host?" (`hosted_channels`), for the voice focus,
    agent-designer's lint and the routing table: a list can't hold every channel
    on the server, so "*" stands for every channel an agent lists. A channel no
    agent lists has no agent to switch to or route to.

An agent reachable everywhere (`channels: ["*"]`: the Concierge, the graph
workers) owns no channel, so it adds none to that list.

Everything here is pure: it reads configs, so the loader, the router, the voice
focus, the Discord side and the lint all agree.
"""
from typing import Any, Iterable, List

# In `channel_hosts`: every channel.
WILDCARD = "*"


def _clean(name: Any) -> str:
    """A channel name or id as configs and Discord compare it: lowercase, without '#'."""
    return name.strip().lstrip("#").strip().lower() if isinstance(name, str) else ""


def _names(value: Any) -> List[str]:
    """A config's channel list as clean names. A value that isn't a list names
    nothing, and an entry that isn't a string is skipped."""
    if not isinstance(value, (list, tuple)):
        return []
    return [name for name in (_clean(item) for item in value) if name]


def hosts(channel_hosts: Any, channel: Any) -> bool:
    """Whether a `channel_hosts` value hosts `channel`: it names it, or holds "*"
    and the channel has a name."""
    name = _clean(channel)
    names = _names(channel_hosts)
    return bool(name) and (WILDCARD in names or name in names)


def named_channels(channel_hosts: Any) -> List[str]:
    """The channels a `channel_hosts` value names itself, without the wildcard.
    The only way to host a channel by id, or a DM."""
    return [name for name in _names(channel_hosts) if name != WILDCARD]


def listed_channels(configs: Iterable[Any]) -> List[str]:
    """Every channel an agent lists in its `channels`, sorted: the channels config
    can name. An agent that lists "*" adds none."""
    listed = set()
    for config in configs:
        channels = _names(config.get("channels") if isinstance(config, dict) else None)
        if WILDCARD not in channels:
            listed.update(channels)
    return sorted(listed)


def hosted_channels(channel_hosts: Any, configs: Iterable[Any]) -> List[str]:
    """The hosted channels config can name, for a list: the names in a
    `channel_hosts` value, in its order, each once, with "*" standing for
    `listed_channels(configs)`. `configs` (every agent's config) is only read for it."""
    hosted: List[str] = []
    for name in _names(channel_hosts):
        hosted.extend(listed_channels(configs) if name == WILDCARD else [name])
    return list(dict.fromkeys(hosted))


def shared_channels(config: Any) -> List[str]:
    """The host's `shared_channels`: channels where no single agent is expected.
    An unaddressed message there gets the host's fixed line, never the notice
    that the channel isn't set up."""
    return _names(config.get("shared_channels") if isinstance(config, dict) else None)
