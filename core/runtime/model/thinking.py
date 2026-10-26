"""Which thinking level a Gemini model call runs at.

The defaults live in `core.config.models` (`THINKING_BY_SURFACE`); this module
decides, per call, which of the possible sources applies. First match wins:

  1. the agent's own pick (`set_thinking`), for its next model call only,
     capped at `VOICE_THINKING_CAP` on a voice turn
  2. the call's own override: `ExecutionContext.thinking_level`, which the
     scheduler sets from a schedule entry's `thinking_level`
  3. the agent's override for this surface: `"thinking"` in its agent.json
  4. the code default for this surface

and the result is clamped to the levels the model accepts.

None means "send nothing", so the model uses its own default. That is the
answer outside an agent run, for stateless internal workers (graph-worker,
graph-worker-low: their callers chose them for a cost point, and that choice
includes the model's own thinking), and for surfaces with no default (`tool`,
`job`).

Resolved at call time, never when a graph is built: compiled graphs are cached
per agent and serve every surface, so the level has to come from the context
of the call in flight.
"""
from typing import Optional

from core.config.models import THINKING_BY_SURFACE, THINKING_LEVELS, VOICE_THINKING_CAP, clamp_thinking_level
from core.loaders.agents_loader import AgentsLoader
from core.runtime.foundation.context import require_context, try_context
from core.runtime.observability.turn_trace import current_turn_trace

PICK_KEY = "thinking_pick"


def pick_thinking(level: str, reason: str) -> str:
    """Records the agent's pick for its next model call; returns the level it will get.

    Called by the `set_thinking` tool. Every pick is logged with its reason, and
    added to the voice turn's trace when there is one.
    """
    if level not in THINKING_LEVELS:
        raise ValueError(f"Unknown thinking level {level!r}; expected one of {list(THINKING_LEVELS)}.")
    ctx = require_context()
    applied = _capped(level, ctx.get_surface())
    ctx.turn_hints[PICK_KEY] = applied
    print(f"[Thinking] agent={ctx.agent_id} surface={ctx.get_surface()} picked={level} "
          f"applied={applied} reason={reason!r}")
    trace = current_turn_trace()
    if trace is not None:
        trace.event("thinking_pick", agent=ctx.agent_id, level=level, applied=applied, reason=reason)
    return applied


def _capped(level: str, surface: str) -> str:
    if surface == "voice" and THINKING_LEVELS.index(level) > THINKING_LEVELS.index(VOICE_THINKING_CAP):
        return VOICE_THINKING_CAP
    return level


def resolve_thinking_level(model_id: str) -> Optional[str]:
    """The level to send for a call to `model_id` in the current context, or None.

    Takes the agent's pending `set_thinking` pick, if any: the pick covers one
    model call, so the call after it is back on the usual sources. The model
    wrapper calls this once per call, before its retry loop, so a retried call
    keeps the level it started with.
    """
    ctx = try_context()
    if ctx is None:
        return None

    config = AgentsLoader().get_agent_config(ctx.agent_id) or {}
    if config.get("stateless"):
        return None

    surface = ctx.get_surface()
    level = (
        ctx.turn_hints.pop(PICK_KEY, None)
        or ctx.thinking_level
        or (config.get("thinking") or {}).get(surface)
        or THINKING_BY_SURFACE.get(surface)
    )
    return clamp_thinking_level(level, model_id)
