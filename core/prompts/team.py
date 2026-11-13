"""The team block: who else the user can talk to, and how to involve them.

In the prod logs, agents asked for a teammate either refused ("I don't have
the system permissions to automatically move your active session") or didn't
know the teammate existed ("I'm ready to design Frankie"). This block lists
every agent with a channel of its own -- name, id, channel, a line on what it
does -- and the two directives a reply can end with
(core/channel/response_parser.py):

- <switch to="goal-setter"/> moves the user. The voice room follows it
  (core/channel/voice/speech/reply.py), and a typed channel shows where to
  find the teammate (core/channel/handoff.py). Handed over cold, a teammate
  made the user repeat everything, so when it should pick up the work, the
  agent writes a brief inside the tag -- <switch to="goal-setter">the request,
  the decisions, the constraints</switch> -- and graphs/main parks it, with a
  capsule of the conversation, for the teammate's first turn.
- <pass to="goal-setter">brief</pass> leaves the teammate a brief and keeps the
  user. With <switch> the only directive, a user who asked to have something
  passed along was moved mid-conversation, and agents moved users who hadn't
  asked to go. Now a switch is for when the user asks to go, and "pass this
  to Daisy" is a pass: its capsule is parked the same way and waits for
  whenever the user reaches the teammate. When the user is done, the agent
  hands them over with a bare <switch>, and the pass's brief goes along
  (graphs/main carries it into the switch's capsule).

The block also says what a <handoff_context> is, and what a message that is
only <handoff_arrival/> and that context means: the user was just handed over
and hasn't said anything yet, so the teammate opens with what it understood
they need, not a greeting. The agents that receive these are the ones with a
channel of their own.

Only an agent with a home channel gets the block, since only those talk with
the user: not the concierge, which hosts every channel and answers none of
them itself, nor the stateless workers. The block changes only when the agents
do, so it sits among the static blocks, for prompt caching
(core/runtime/graph_builder.py).

The open-passes block (`get_open_passes_prompt`) is the conversation's: the
passes the agent made in it that haven't reached their teammates yet, so that
it hands the user over to the right one when they're done, and doesn't pass
the same thing twice. It changes when a pass is made or picked up, so it sits
with the per-conversation blocks, after the channel's.
"""
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from core.loaders.agents_loader import AgentsLoader
from core.prompts.base import Prompt
from core.runtime.foundation.routing import home_channel
from core.storage.handoff_capsules import HandoffCapsuleStore
from core.util.local_time import get_local_timezone

# A teammate's line keeps the first sentence of its description, at most this long.
DESCRIPTION_CHARS = 90
# An open pass's line keeps this much of its brief: enough to tell passes apart.
PASS_BRIEF_CHARS = 200


@dataclass
class TeamPrompt(Prompt):
    """The other agents with a channel of their own, one line each."""

    template = "system/team"
    teammates: str


@dataclass
class OpenPassesPrompt(Prompt):
    """The passes an agent made in this conversation that still wait for their
    teammates, one line each (`open_pass_line`)."""

    template = "system/open_passes"
    passes: str


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


def get_open_passes_prompt(agent_id: str, channel_name: str, loader: Any = None,
                           now: Optional[float] = None) -> str:
    """The open-passes block for `agent_id`'s conversation in `channel_name`:
    each pass it made there that still waits for its teammate, oldest first
    (HandoffCapsuleStore.open_passes).

    "" when none waits, without an agent or a channel, and on any error: the
    block is read on every model call, and must never break the prompt.
    """
    if not agent_id or not channel_name:
        return ""
    try:
        passes = HandoffCapsuleStore().open_passes(agent_id, channel_name, now=now)
        if not passes:
            return ""
        loader = loader if loader is not None else AgentsLoader()
        lines = [open_pass_line(parked, loader, now) for parked in passes]
        return OpenPassesPrompt(passes="\n".join(lines)).render()
    except Exception as e:
        print(f"[team] No open passes for {agent_id} in #{channel_name}: {type(e).__name__}: {e}")
        return ""


def open_pass_line(parked: Any, loader: Any, now: Optional[float] = None) -> str:
    """One open pass: "- Daisy [day-planner], passed 17:16: Book the dentist…".
    The time is local, with the day when it wasn't today; the brief is cut at
    PASS_BRIEF_CHARS."""
    config = loader.get_agent_config(parked.to_agent) or {}
    zone = get_local_timezone()
    when = datetime.fromtimestamp(parked.created_at, zone)
    today = datetime.fromtimestamp(time.time() if now is None else now, zone).date()
    stamp = when.strftime("%H:%M") if when.date() == today else when.strftime("%a %H:%M")
    brief = clip(parked.brief, PASS_BRIEF_CHARS)
    line = f"- {config.get('name') or parked.to_agent} [{parked.to_agent}], passed {stamp}"
    return f"{line}: {brief}" if brief else line


def short_description(description: Any, limit: int = DESCRIPTION_CHARS) -> str:
    """The first sentence of `description`, cut at a word within `limit` characters."""
    text = " ".join(str(description or "").split())
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return clip(first, limit)


def clip(text: Any, limit: int) -> str:
    """`text` on one line, cut at a word within `limit` characters."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
