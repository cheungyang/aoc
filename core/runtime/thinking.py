"""Which thinking level a Gemini model call runs at.

The defaults live in `core.util.models` (`THINKING_BY_SURFACE`); this module
decides, per call, which of the possible sources applies. First match wins:

  1. the call's own override: `ExecutionContext.thinking_level`, which the
     scheduler sets from a schedule entry's `thinking_level`
  2. the agent's override for this surface: `"thinking"` in its agent.json
  3. the code default for this surface

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


def resolve_thinking_level(model_id: str) -> Optional[str]:
    """The level to send for a call to `model_id` in the current context, or None."""
    from core.runtime.execution_context import try_context
    from core.util.models import THINKING_BY_SURFACE, clamp_thinking_level

    ctx = try_context()
    if ctx is None:
        return None

    from core.loaders.agents_loader import AgentsLoader
    config = AgentsLoader().get_agent_config(ctx.agent_id) or {}
    if config.get("stateless"):
        return None

    surface = ctx.get_surface()
    level = (
        ctx.thinking_level
        or (config.get("thinking") or {}).get(surface)
        or THINKING_BY_SURFACE.get(surface)
    )
    return clamp_thinking_level(level, model_id)
