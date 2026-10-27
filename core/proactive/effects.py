"""What a tool's call means to the proactive team: one table, for every tool.

The taps (taps.py) hear about every finished tool call through the tool hooks
(core/tooling/hooks.py). This table says which calls the team cares about,
and how:

  TRIGGER  news for the team: the Switchboard reads the turn at once
  CONTEXT  background: the Switchboard reads it with the rest of the
           conversation, once that goes quiet (service.py)

A tool that isn't here is ignored, and so is any call that failed. The tools
themselves know nothing of this. Giving a tool an effect is one line here, and
tests/core/proactive/test_effects.py checks that every tool and permission
bundle named here exists, so the table can't quietly go stale.

`actions` narrows a row to some of the tool's actions. Each one is an action
name or one of the tool's permission bundles (`@change`,
core/permissions/bundles.py), so the row follows the tool when it adds an
action. A tool may have several rows, for different actions. A call is a
trigger if any of its actions is one.

`paths` keeps the paths a call changed, as turn context, instead of the call
itself (filesystem: "wiki/topics/Prompt_Caching.md (write)").
"""
import functools
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, Optional, Tuple

from core.permissions.bundles import expand_actions

TRIGGER = "trigger"
CONTEXT = "context"
ROLES = (TRIGGER, CONTEXT)


@dataclass(frozen=True)
class ProactiveEffect:
    """One row: successful calls of `tool` (of its `actions`, if given) mean `role`."""
    tool: str
    role: str
    actions: Tuple[str, ...] = ()
    paths: bool = False

    def __post_init__(self):
        if self.role not in ROLES:
            raise ValueError(f"{self.tool}: role must be one of {ROLES}, not {self.role!r}")

    def covers(self, actions: Iterable[str]) -> bool:
        """Whether a call performing `actions` is one this row means."""
        return not self.actions or any(action in counted_actions(self) for action in actions)


PROACTIVE_EFFECTS: Tuple[ProactiveEffect, ...] = (
    # News for the team.
    ProactiveEffect("ledger_append", TRIGGER),        # a receipt saved
    ProactiveEffect("kitchen_inventory", TRIGGER),    # food bought, eaten or thrown out
    ProactiveEffect("calendar_add", TRIGGER),         # a plan made
    ProactiveEffect("log_workout_session", TRIGGER),  # training load: Daisy's energy, Eric's outings
    # Background for the Switchboard, never a trigger on its own.
    ProactiveEffect("write_weekly_review", CONTEXT),  # the review was already talked through with you
    ProactiveEffect("graph_call", CONTEXT),           # a coding or content run started
    # Device and automation changes: mostly housekeeping. Reads are ignored.
    ProactiveEffect("home_assistant", CONTEXT, actions=("@control", "@admin")),
    # Notes changed: the paths, not the call. Reads are ignored.
    ProactiveEffect("filesystem", CONTEXT, actions=("@change",), paths=True),
)


@functools.lru_cache(maxsize=None)
def counted_actions(effect: ProactiveEffect) -> FrozenSet[str]:
    """The action names `effect.actions` stands for, with the tool's bundles expanded."""
    return frozenset(expand_actions(effect.tool, effect.actions))


@functools.lru_cache(maxsize=1)
def _rows() -> Dict[str, Tuple[ProactiveEffect, ...]]:
    rows: Dict[str, Tuple[ProactiveEffect, ...]] = {}
    for effect in PROACTIVE_EFFECTS:
        rows[effect.tool] = rows.get(effect.tool, ()) + (effect,)
    return rows


def effects_for(tool: str) -> Tuple[ProactiveEffect, ...]:
    """The rows for `tool`; empty for a tool the team ignores."""
    return _rows().get(tool, ())


def role_of(tool: str, actions: Iterable[str]) -> Optional[str]:
    """What a successful call of `tool` performing `actions` is to the team: TRIGGER, CONTEXT, or None."""
    actions = list(actions)
    roles = {effect.role for effect in effects_for(tool) if effect.covers(actions)}
    return TRIGGER if TRIGGER in roles else (CONTEXT if CONTEXT in roles else None)


def keeps_paths(tool: str) -> bool:
    """Whether a call of `tool` counts by the paths it changed (`paths`), not as an effect."""
    return any(effect.paths for effect in effects_for(tool))
