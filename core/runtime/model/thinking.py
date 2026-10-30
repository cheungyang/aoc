"""Which thinking level a Gemini model call runs at.

The defaults live in `core.config.models` (`THINKING_BY_SURFACE`); this module
decides, per call, which of the possible sources applies. First match wins:

  1. the model's own pick: a `<next_thinking>LEVEL</next_thinking>` marker in
     its previous response of the turn, the one that made the tool calls this
     call reasons over; for that one call, capped at `VOICE_THINKING_CAP` on a
     voice turn
  2. the call's own override: `ExecutionContext.thinking_level`, which the
     scheduler sets from a schedule entry's `thinking_level`
  3. the agent's override for this surface: `"thinking"` in its agent.json
  4. the code default for this surface

and the result is clamped to the levels the model accepts.

The marker rides in the response that makes the tool calls, so a pick costs no
model call of its own, and a final response has no next call to pick for. It
replaced the `set_thinking` tool, which the model mostly called alone: a whole
extra model call each time. The model wrapper (core/runtime/model/retry.py)
takes the marker out as the response streams, so no transcript, history,
channel or speech shows it. The prompt rule that offers it is in
core/prompts/templates/system/tool_rules.md.

None means "send nothing", so the model uses its own default. That is the
answer outside an agent run, for stateless internal workers (graph-worker,
graph-worker-low: their callers chose them for a cost point, and that choice
includes the model's own thinking), and for surfaces with no default (`tool`,
`job`).

Resolved at call time, never when a graph is built: compiled graphs are cached
per agent and serve every surface, so the level has to come from the context
of the call in flight.
"""
import re
from typing import Optional, Tuple

from core.config.models import THINKING_BY_SURFACE, THINKING_LEVELS, VOICE_THINKING_CAP, clamp_thinking_level
from core.loaders.agents_loader import AgentsLoader
from core.runtime.foundation.context import try_context
from core.runtime.observability.turn_trace import current_turn_trace

PICK_KEY = "thinking_pick"

MARKER_OPEN = "<next_thinking>"
MARKER_CLOSE = "</next_thinking>"
_MARKER_BODY = 20  # a level is one word; anything longer between the tags isn't a marker
_MARKER = re.compile(r"<next_thinking>(.{0,%d}?)</next_thinking>[ \t]*\n?" % _MARKER_BODY,
                     re.IGNORECASE | re.DOTALL)
_MARKER_MAX = len(MARKER_OPEN) + _MARKER_BODY + len(MARKER_CLOSE)


def take_marker(text: str) -> Tuple[str, Optional[str]]:
    """`text` without its markers, and the level the last one named (None without one)."""
    levels = []

    def drop(match):
        levels.append(match.group(1).strip().lower())
        return ""

    return _MARKER.sub(drop, text), (levels[-1] if levels else None)


class MarkerFilter:
    """Takes the marker out of a response's text as it streams, even split across chunks.

    Text goes straight out, except from a `<` that may open the marker: that is
    held until it does or can't, a few characters at most. `level` is the level
    the last marker named.
    """

    def __init__(self):
        self.level: Optional[str] = None
        self._held = ""

    def feed(self, text: str) -> str:
        """What can go out now, of whatever was held plus `text`."""
        buf, out = self._held + text, []
        self._held = ""
        while buf:
            start = buf.find("<")
            if start == -1:
                out.append(buf)
                break
            out.append(buf[:start])
            buf = buf[start:]
            match = _MARKER.match(buf)
            if match:
                self.level = match.group(1).strip().lower()
                buf = buf[match.end():]
            elif _may_open(buf.lower()):
                self._held = buf
                break
            else:
                out.append("<")
                buf = buf[1:]
        return "".join(out)

    def flush(self) -> str:
        """What's still held when the response ends: the start of a marker that never came."""
        held, self._held = self._held, ""
        return held


def _may_open(lower: str) -> bool:
    """Whether text starting with `<` could still turn out to be a marker."""
    if len(lower) <= len(MARKER_OPEN):
        return MARKER_OPEN.startswith(lower)
    return lower.startswith(MARKER_OPEN) and MARKER_CLOSE not in lower and len(lower) < _MARKER_MAX


def pick_thinking(level: str) -> Optional[str]:
    """Records the model's pick for its next call in this turn; returns the level it will get.

    Called by the model wrapper when a response carries the marker. A level that
    isn't one, or a pick outside an agent run, records nothing and returns None.
    Every pick is logged, and added to the voice turn's trace when there is one.
    """
    ctx = try_context()
    if ctx is None:
        return None
    if level not in THINKING_LEVELS:
        print(f"[Thinking] agent={ctx.agent_id} ignored <next_thinking>{level}</next_thinking>: not a level")
        return None
    applied = _capped(level, ctx.get_surface())
    ctx.turn_hints[PICK_KEY] = applied
    print(f"[Thinking] agent={ctx.agent_id} surface={ctx.get_surface()} picked={level} applied={applied}")
    trace = current_turn_trace()
    if trace is not None:
        trace.event("thinking_pick", agent=ctx.agent_id, level=level, applied=applied)
    return applied


def _capped(level: str, surface: str) -> str:
    if surface == "voice" and THINKING_LEVELS.index(level) > THINKING_LEVELS.index(VOICE_THINKING_CAP):
        return VOICE_THINKING_CAP
    return level


def resolve_thinking_level(model_id: str) -> Optional[str]:
    """The level to send for a call to `model_id` in the current context, or None.

    Takes the model's pending pick, if any: the pick covers one model call, so
    the call after it is back on the usual sources. The model wrapper calls this
    once per call, before its retry loop, so a retried call keeps the level it
    started with.
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
