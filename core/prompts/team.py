"""The team block: who else the user can talk to, and how to hand them over.

In the prod logs, agents asked for a teammate either refused ("I don't have
the system permissions to automatically move your active session") or didn't
know the teammate existed ("I'm ready to design Frankie"). This block lists
every agent with a channel of its own -- name, id, channel, a line on what it
does -- and the one directive that moves the user: a reply ending with
<switch to="goal-setter"/> (core/channel/response_parser.py). The voice room
follows it (core/channel/voice/speech/reply.py), and a typed channel shows where to
find the teammate (core/channel/handoff.py).

Handed over cold, a teammate made the user repeat everything. So when the
teammate should pick up the work, the agent writes a brief inside the tag --
<switch to="goal-setter">the request, the decisions, the constraints</switch>
-- and graphs/main parks it, with a capsule of the conversation, for the
teammate's first turn. The block also says what that <handoff_context> is:
the agents that receive one are the ones with a channel of their own.

Only an agent with a home channel gets the block, since only those talk with
the user: not the concierge, which hosts every channel and answers none of
them itself, nor the stateless workers. The block changes only when the agents
do, so it sits among the static blocks, for prompt caching
(core/runtime/graph_builder.py).
"""
import re
from dataclasses import dataclass
from typing import Any

from core.loaders.agents_loader import AgentsLoader
from core.prompts.base import Prompt
from core.runtime.foundation.routing import home_channel

# A teammate's line keeps the first sentence of its description, at most this long.
DESCRIPTION_CHARS = 90


@dataclass
class TeamPrompt(Prompt):
    """The other agents with a channel of their own, one line each."""

    template = "system/team"
    teammates: str


def get_team_prompt(agent_id: str, loader: Any = None) -> str:
    """The team block for `agent_id`: "" for an agent that doesn't talk with
    the user (it has no home channel), or that has no teammates."""
    loader = loader if loader is not None else AgentsLoader()
    if not home_channel(agent_id, loader):
        return ""
    lines = []
    for other in loader.list_agent_ids():
        home = home_channel(other, loader) if other != agent_id else None
        if not home:
            continue
        config = loader.get_agent_config(other) or {}
        about = short_description(config.get("description"))
        lines.append(f"- {config.get('name') or other} [{other}], #{home}" + (f": {about}" if about else ""))
    return TeamPrompt(teammates="\n".join(lines)).render() if lines else ""


def short_description(description: Any, limit: int = DESCRIPTION_CHARS) -> str:
    """The first sentence of `description`, cut at a word within `limit` characters."""
    text = " ".join(str(description or "").split())
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    if len(first) <= limit:
        return first
    return first[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
