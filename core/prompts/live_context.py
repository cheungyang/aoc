"""Live Context: what's true about the user right now, from the teammates' signals.

A LIVE_CONTEXT system block, for an agent that keeps memory: the active
situations on the proactive Board whose tags it reads (its `memory_topics`),
newest first, at most MAX_LINES. One line each, with no source agent named:

    - The user and family are in Lake Tahoe Oct 23–27 (until 2026-10-27)

It's working memory, next to the dream's long-term memory: nothing curates it,
and a line goes on its own, when its `until` passes or MAX_AGE_S after it was
learned. SignalStore.live_situations picks the lines.

Its second part is the agent's open follow-ups (core/storage/follow_ups.py),
soonest `until` first, at most MAX_FOLLOW_UPS, so it knows what it said it would
keep working on. It won't offer one it already has, it can answer "what are you
watching?", and it closes one when you say it's done:

    - #12 Business award seats SFO→MAD · every 2d until 2026-12-15

That part tells the agent to close one with `follow_up`, so only a run with the
tool sees it (core/loaders/tools_loader.gets_follow_up): not a run another agent
asked for, nor one with no context to say whose run it is.

The block is empty, and so left out, unless the switch (`PROACTIVE_DELIVERY`)
is on. It's empty too for a stateless agent, and a stateless or non-recording
run (a job, a dream, a replay): a dream that saw it could write it into
long-term memory, uncurated. An agent with no memory topics has no situations,
but still sees its follow-ups. Each part is left out when it has no line.

core/runtime/graph_builder.py puts it right after the agent's memory and before
the channel block: it changes a few times a day. It's rendered on every model
call, so each agent's block is kept for CACHE_S: one with its follow-ups, and
one without.
"""
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.config.settings import Config
from core.knowledge.memory.inject import memory_topics
from core.loaders.tools_loader import gets_follow_up
from core.prompts.base import Prompt
from core.runtime.foundation.context import try_context
from core.storage.follow_ups import FollowUp, FollowUpStore, Status, cadence_text
from core.storage.signals import SignalStore
from core.util.local_time import get_local_timezone

MAX_LINES = 8
MAX_FOLLOW_UPS = 5
# How long a situation can be shown, from when it was learned, whatever its `until`.
MAX_AGE_S = 14 * 86400
# How long an agent's block is kept: it changes a few times a day, and is asked for on every model call.
CACHE_S = 60.0

# (agent_id, whether its follow-ups show) -> (when it was rendered, the block)
_cache: Dict[Tuple[str, bool], Tuple[float, str]] = {}


@dataclass
class LiveContextPrompt(Prompt):
    """The LIVE_CONTEXT block: one line per live situation (`live_lines`), and one per open
    follow-up of the agent's (`follow_up_lines`). A part with no line is left out."""

    template = "system/live_context"

    lines: str
    follow_ups: str


def live_lines(situations: List[Dict[str, Any]]) -> str:
    """The block's lines: each situation's gist and its `until`."""
    return "\n".join(f"- {s['gist']} (until {s['until']})" for s in situations)


def follow_up_lines(follow_ups: List[FollowUp]) -> str:
    """The follow-ups' lines: each one's id, goal, cadence and `until`."""
    return "\n".join(f"- #{f.id} {f.goal} · every {cadence_text(f.every_s)} until {f.until}" for f in follow_ups)


def get_live_context_prompt(agent_id: str, config: Optional[dict] = None, *, now: Optional[float] = None) -> str:
    """The LIVE_CONTEXT block for `agent_id`, or "" (module docstring). Kept for CACHE_S per
    agent, and per whether its follow-ups show: only to a run that has the follow_up tool."""
    if not Config().proactive_delivery:
        return ""
    ctx = try_context()
    if ctx is not None and (ctx.is_stateless() or not ctx.record_memory):
        return ""
    if config is None:
        from core.loaders.agents_loader import AgentsLoader
        config = AgentsLoader().get_agent_config(agent_id) or {}
    if config.get("stateless"):
        return ""
    with_follow_ups = ctx is not None and gets_follow_up(ctx, config)
    now = time.time() if now is None else now
    key = (agent_id, with_follow_ups)
    cached = _cache.get(key)
    if cached is not None and now - cached[0] < CACHE_S:
        return cached[1]
    block = _render(agent_id, config, now, with_follow_ups)
    _cache[key] = (now, block)
    return block


def _render(agent_id: str, config: dict, now: float, with_follow_ups: bool) -> str:
    """The block, or "" when neither part has a line. Never raises: a part that can't be
    read is left out, and the turn goes on."""
    situations = _situations(agent_id, config, now)
    follow_ups = _follow_ups(agent_id) if with_follow_ups else ""
    if not situations and not follow_ups:
        return ""
    return LiveContextPrompt(lines=situations, follow_ups=follow_ups).render()


def _situations(agent_id: str, config: dict, now: float) -> str:
    """The live situations' lines, from the Board, or ""."""
    try:
        topics = memory_topics(config, agent_id=agent_id)
        if not topics:
            return ""
        today = datetime.fromtimestamp(now, get_local_timezone()).strftime("%Y-%m-%d")
        situations = SignalStore().live_situations(topics, today=today, since=now - MAX_AGE_S, limit=MAX_LINES)
    except Exception as e:
        print(f"[live_context] No Live Context for {agent_id}: {type(e).__name__}: {e}")
        return ""
    return live_lines(situations)


def _follow_ups(agent_id: str) -> str:
    """The agent's own open follow-ups' lines, soonest `until` first, or ""."""
    try:
        follow_ups = FollowUpStore().list(agent_id=agent_id, status=Status.OPEN)
    except Exception as e:
        print(f"[live_context] No follow-ups in {agent_id}'s Live Context: {type(e).__name__}: {e}")
        return ""
    return follow_up_lines(sorted(follow_ups, key=lambda f: (f.until, f.id))[:MAX_FOLLOW_UPS])
