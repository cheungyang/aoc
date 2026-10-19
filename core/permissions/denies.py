"""Deny entries in path grants: `"!<path>": [actions]` refuses those actions under <path>.

A grant key that starts with `!` doesn't grant anything. It names actions to
refuse under a path, and a refusal beats any grant, from agent.json, a skill or
a graph alike. It exists for one job: agent-designer manages `agents/`, but the
code inside each agent folder changes only through a reviewed PR:

    "agents/": ["@manage"],
    "!agents/*/tools/": ["@change"]

`*` (and `?`, `[...]`) match within one folder name, so `agents/*/tools/`
covers `agents/fitness-coach/tools/workout_setup.py` but not a deeper
`agents/x/y/tools/`. Wildcards work only in deny keys; grant keys keep their
plain path semantics. Deny entries apply to path-matched tools (filesystem,
bash), not to tools with their own selector matcher (Home Assistant).
"""
import fnmatch
import os

DENY_PREFIX = "!"
_WILDCARD = ("*", "?", "[")


def is_deny(key: str) -> bool:
    return isinstance(key, str) and key.startswith(DENY_PREFIX)


def deny_covers(key: str, target: str, root: str, agent_id: str) -> bool:
    """Whether deny `key` covers `target`, an absolute path with symlinks resolved.

    `root` is the repo root (relative keys are under it), and `<agent_id>` in a
    key is the agent's id, as in grant keys. The part of the key before its
    first wildcard has its symlinks resolved too, so `pkm/...` keys compare
    like grants do.
    """
    pattern = os.path.expanduser(key[len(DENY_PREFIX):].replace("<agent_id>", agent_id))
    if not os.path.isabs(pattern):
        pattern = os.path.join(root, pattern)
    parts = os.path.normpath(pattern).split(os.sep)
    first_wild = next((i for i, part in enumerate(parts) if any(c in part for c in _WILDCARD)), len(parts))
    fixed = os.path.realpath(os.sep.join(parts[:first_wild]) or os.sep)
    pattern_parts = fixed.split(os.sep) + parts[first_wild:]
    target_parts = os.path.normpath(target).split(os.sep)
    if len(target_parts) < len(pattern_parts):
        return False
    return all(fnmatch.fnmatchcase(t, p) for t, p in zip(target_parts, pattern_parts))
