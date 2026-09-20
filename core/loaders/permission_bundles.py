"""Named action bundles for tool permissions.

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

Bundles live in code, not configuration. `@write` therefore means the same thing
for every agent and cannot be quietly widened in one `agent.json`.
"""
from typing import Any, Dict, Iterable, List, Set

BUNDLE_PREFIX = "@"

BUNDLES: Dict[str, Dict[str, List[str]]] = {
    "filesystem": {
        "@read": ["read", "read_image", "ls", "find", "grep"],
        "@write": ["@read", "write", "overwrite", "append", "replace_block"],
        # `move` is here rather than in @write because renaming a file can break
        # references elsewhere, which is a different kind of consequence from
        # editing one in place.
        "@manage": ["@write", "move", "delete", "rmdir"],
    },
    "home_assistant": {
        # Every read action. Safe to run unattended, so this is the grant most
        # agents should have and nothing more.
        "@observe": [
            "inventory", "search_registry", "get_state", "list_entities",
            "get_config", "list_services", "render_template", "history",
            "logbook", "error_log", "check_config", "list_automations",
            "get_automation", "live_context",
        ],
        # Actuating existing things. Deliberately does not include @observe: the
        # two are granted against different selectors (observe on "*", control on
        # "light.*"), so folding one into the other would silently widen the
        # narrow grant to everything the broad one covers.
        "@control": ["call_service"],
        # Creating and changing stored config. Every action here requires human
        # confirmation at the guard layer regardless of the grant.
        "@author": [
            "upsert_automation", "upsert_script", "upsert_scene",
            "upsert_helper", "reload",
        ],
        # Authoring plus destruction.
        "@admin": [
            "@author", "delete_automation", "delete_script", "delete_scene",
        ],
    },
}


class BundleError(ValueError):
    """A bundle definition is malformed. Raised at validation time, not per-call."""


def is_bundle(token: Any) -> bool:
    return isinstance(token, str) and token.startswith(BUNDLE_PREFIX)


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
    definitions = BUNDLES.get(tool_id, {})
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


def expand_permissions(merged_tools: Dict[str, Any]) -> Dict[str, Any]:
    """Expands every bundle in a merged permission map, in place.

    Tools with no bundles defined pass through untouched, so this is safe to call
    unconditionally on the whole map.
    """
    for tool_id, scope in list(merged_tools.items()):
        if tool_id in BUNDLES:
            merged_tools[tool_id] = expand_scope(tool_id, scope)
    return merged_tools


def validate() -> None:
    """Checks every bundle resolves and terminates. Called by the test suite.

    Run as a test rather than at import: a malformed bundle should fail the build
    loudly, not take down a running agent at startup.
    """
    for tool_id, definitions in BUNDLES.items():
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

            # Expansion must reach at least one concrete action, or the bundle is
            # a grant that grants nothing.
            expanded = expand_actions(tool_id, [name])
            if not expanded:
                raise BundleError(f"{tool_id}: bundle '{name}' expands to nothing.")
            if any(is_bundle(action) for action in expanded):
                raise BundleError(
                    f"{tool_id}: bundle '{name}' still contains bundle names after "
                    f"expansion: {[a for a in expanded if is_bundle(a)]}"
                )
