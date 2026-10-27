"""Live Context: what's true about the user right now, from the teammates' signals.

Stage S4, part 2 (review record §6.4). A LIVE_CONTEXT system block, for an agent
that keeps memory: the active situations on the proactive Board whose tags it
reads (its `memory_topics`), newest first, at most MAX_LINES. One line each,
with no source agent named:

    - The user and family are in Lake Tahoe Oct 23–27 (until 2026-10-27)

It's working memory, next to the dream's long-term memory: nothing curates it,
and a line goes on its own, when its `until` passes or MAX_AGE_S after it was
learned. SignalStore.live_situations picks the lines.

The block is empty, and so left out, unless the switch (`PROACTIVE_DELIVERY`)
is on. It's empty too for a stateless agent, an agent with no memory topics,
and a stateless or non-recording run (a job, a dream, a replay): a dream that
saw it could write it into long-term memory, uncurated.

core/runtime/graph_builder.py puts it right after the agent's memory and before
the channel block: it changes a few times a day. It's rendered on every model
call, so each agent's block is kept for CACHE_S.
"""
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.config.settings import Config
from core.knowledge.memory.inject import memory_topics
from core.prompts.base import Prompt
from core.runtime.foundation.context import try_context
from core.storage.signals import SignalStore
from core.util.local_time import get_local_timezone

MAX_LINES = 8
# How long a situation can be shown, from when it was learned, whatever its `until`.
MAX_AGE_S = 14 * 86400
# How long an agent's block is kept: it changes a few times a day, and is asked for on every model call.
CACHE_S = 60.0

# agent_id -> (when it was rendered, the block)
_cache: Dict[str, Tuple[float, str]] = {}


@dataclass
class LiveContextPrompt(Prompt):
    """The LIVE_CONTEXT block: one line per live situation (`live_lines`)."""

    template = "system/live_context"

    lines: str


def live_lines(situations: List[Dict[str, Any]]) -> str:
    """The block's lines: each situation's gist and its `until`."""
    return "\n".join(f"- {s['gist']} (until {s['until']})" for s in situations)


def get_live_context_prompt(agent_id: str, config: Optional[dict] = None, *, now: Optional[float] = None) -> str:
    """The LIVE_CONTEXT block for `agent_id`, or "" (module docstring). Kept per agent for CACHE_S."""
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
    now = time.time() if now is None else now
    cached = _cache.get(agent_id)
    if cached is not None and now - cached[0] < CACHE_S:
        return cached[1]
    block = _render(agent_id, config, now)
    _cache[agent_id] = (now, block)
    return block


def _render(agent_id: str, config: dict, now: float) -> str:
    """The block from the Board, or "" when there's nothing to show. Never raises: a turn
    goes on without its Live Context rather than fail."""
    try:
        topics = memory_topics(config, agent_id=agent_id)
        if not topics:
            return ""
        today = datetime.fromtimestamp(now, get_local_timezone()).strftime("%Y-%m-%d")
        situations = SignalStore().live_situations(topics, today=today, since=now - MAX_AGE_S, limit=MAX_LINES)
    except Exception as e:
        print(f"[live_context] No Live Context for {agent_id}: {type(e).__name__}: {e}")
        return ""
    return LiveContextPrompt(lines=live_lines(situations)).render() if situations else ""
