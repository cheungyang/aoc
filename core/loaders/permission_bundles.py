"""Expansion of named action bundles in tool permissions.

`agent.json` grants name individual actions, which is precise but verbose: a
read-only Home Assistant grant is fourteen strings, and a filesystem grant that
means "can edit files" is nine. Long lists are not just tedious to write -- they
drift between agents, and they make the blanket `{}` shorthand (which means
allow-all) look attractive by comparison. That is the failure this module exists
to prevent.

A bundle is a name beginning with `@` that stands for a set of actions:

    "home_assistant": { "*": ["@observe"], "light.*": ["@control"] }

Bundles may reference other bundles, so `@admin` is defined as `@author` plus the
deletes rather than restating it. Expansion happens once in `ToolsLoader`, after
all merging and before the permissions cache, so `check_permission` never sees an
`@` name and did not need to change.

**This module holds the mechanism, not the definitions.** Each tool declares its
own bundles as a module-level `PERMISSION_BUNDLES` dict, next to the actions they
name -- see `tools/filesystem.py` and `tools/home_assistant.py`. A central
registry would be a second place to edit every time an action is added, and the
copy that lives away from the code is the one that goes stale.

Bundles are still code, not configuration: `@write` means the same thing for
every agent and cannot be quietly widened in one `agent.json`.
"""
import logging
from typing import Any, Dict, Iterable, List, Set

from core.loaders.tool_declarations import clear_cache as _clear_declarations
from core.loaders.tool_declarations import declaration

logger = logging.getLogger(__name__)

BUNDLE_PREFIX = "@"

# The attribute a tool module exposes to declare its bundles.
BUNDLES_ATTR = "PERMISSION_BUNDLES"


class BundleError(ValueError):
    """A bundle definition is malformed. Raised by validate(), not per-call."""


def is_bundle(token: Any) -> bool:
    return isinstance(token, str) and token.startswith(BUNDLE_PREFIX)


def definitions_for(tool_id: str) -> Dict[str, List[str]]:
    """Returns a tool's bundle definitions, or `{}` if it declares none.

    `{}` is the safe default. With no definitions, `@` names stay unexpanded,
    and an unexpanded name matches no action (see `expand_actions`), so a tool
    that fails to import denies rather than grants.
    """
    declared = declaration(tool_id, BUNDLES_ATTR, None)
    return declared if isinstance(declared, dict) else {}


def clear_cache() -> None:
    """Forgets discovered definitions. For tests and hot reload."""
    _clear_declarations()


def expand_actions(tool_id: str, actions: Iterable[str]) -> List[str]:
    """Resolves bundle names in one action list.

    Preserves order of first appearance and de-duplicates, so the result is
    stable and diffable rather than set-ordered.

    Unknown bundle names are kept verbatim instead of being dropped. That looks
    odd until you consider what dropping would do to a grant of exactly
    `["@typo"]`: it would become `[]`, and an empty list means *allow-all* in
    `check_permission`. A typo would silently grant everything. Keeping the
    literal leaves the list non-empty and matching no real action, so the grant
    fails closed. `validate()` is what actually catches the typo.
    """
    definitions = definitions_for(tool_id)
    resolved: List[str] = []
    seen: Set[str] = set()

    def walk(items: Iterable[str], trail: Set[str]) -> None:
        for item in items:
            if is_bundle(item) and item in definitions:
                # A bundle that (directly or transitively) contains itself would
                # recurse forever. `trail` is the set of bundles already open on
                # this branch; re-entering one is a definition bug, and skipping
                # it here keeps expansion total so a bad definition cannot hang
                # the loader at startup.
                if item in trail:
                    continue
                walk(definitions[item], trail | {item})
                continue
            if item not in seen:
                seen.add(item)
                resolved.append(item)

    walk(list(actions or []), set())
    return resolved


def expand_scope(tool_id: str, scope: Any) -> Any:
    """Expands whichever grant shape a tool uses.

    Grants are either a list of actions or a selector -> actions dict. Anything
    else is returned untouched: this is sugar, and it must not be the thing that
    rejects a grant shape the loader would otherwise have accepted.
    """
    if isinstance(scope, list):
        return expand_actions(tool_id, scope)
    if isinstance(scope, dict):
        return {
            selector: (expand_actions(tool_id, actions) if isinstance(actions, list) else actions)
            for selector, actions in scope.items()
        }
    return scope


def _mentions_a_bundle(scope: Any) -> bool:
    """Whether a grant references any `@` name at all.

    Checked before touching a tool module, so the common case -- grants that name
    actions directly -- never pays an import to discover bundles it will not use.
    """
    if isinstance(scope, list):
        return any(is_bundle(item) for item in scope)
    if isinstance(scope, dict):
        return any(
            isinstance(actions, list) and any(is_bundle(item) for item in actions)
            for actions in scope.values()
        )
    return False


def expand_permissions(merged_tools: Dict[str, Any]) -> Dict[str, Any]:
    """Expands every bundle in a merged permission map, in place."""
    for tool_id, scope in list(merged_tools.items()):
        if _mentions_a_bundle(scope):
            merged_tools[tool_id] = expand_scope(tool_id, scope)
    return merged_tools


def validate(tool_id: str) -> None:
    """Checks one tool's bundles resolve and terminate. Called by the test suite.

    Run as a test rather than at import: a malformed bundle should fail the build
    loudly, not take down a running agent at startup.
    """
    definitions = definitions_for(tool_id)

    for name, members in definitions.items():
        if not name.startswith(BUNDLE_PREFIX):
            raise BundleError(f"{tool_id}: bundle name '{name}' must start with '{BUNDLE_PREFIX}'.")
        if not members:
            raise BundleError(f"{tool_id}: bundle '{name}' is empty.")

        for member in members:
            if is_bundle(member) and member not in definitions:
                raise BundleError(
                    f"{tool_id}: bundle '{name}' references undefined bundle '{member}'."
                )

        # Expansion must reach at least one concrete action, or the bundle is a
        # grant that grants nothing.
        expanded = expand_actions(tool_id, [name])
        if not expanded:
            raise BundleError(f"{tool_id}: bundle '{name}' expands to nothing.")
        if any(is_bundle(action) for action in expanded):
            raise BundleError(
                f"{tool_id}: bundle '{name}' still contains bundle names after "
                f"expansion: {[a for a in expanded if is_bundle(a)]}"
            )
