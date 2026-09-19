"""Which backend serves which action.

This table is the answer to "should the agent call MCP or the REST API?" -- and
the answer is that the agent never decides. Backend selection is the same class
of choice as which HTTP verb to use: there is exactly one right answer per
action, it is not a judgment call, and surfacing it to a model would only create
opportunities to get it wrong. Teaching it in a skill would also rot, since an
upgrade that moves a capability between planes would silently invalidate prose
the model had already read.

Keeping it as data means one place to change, zero prompt tokens, and a routing
decision that can be unit-tested.
"""

REST = "rest"
WS = "ws"
MCP = "mcp"

# Read actions. Every one of these is safe to run unattended.
READ_BACKENDS = {
    # State and configuration: plain REST.
    "get_state": REST,
    "list_entities": REST,
    "get_config": REST,
    "list_services": REST,
    "render_template": REST,
    "history": REST,
    "logbook": REST,
    "error_log": REST,
    "check_config": REST,
    "list_automations": REST,
    "get_automation": REST,
    # Registries: only the WebSocket API exposes these. This is also the only
    # way to see disabled and hidden entities, which /api/states omits.
    "search_registry": WS,
    "inventory": WS,
    # HA-authored natural-language summary of exposed entities. Cheaper to let
    # HA build it than to reconstruct it from the registries. Arrives in Phase 6.
    "live_context": MCP,
}

# Mutating actions. Declared here so the guard layer and the permission model can
# reason about the full vocabulary before the actions themselves are implemented
# (Phase 4) -- a guard that only knows about actions that already exist is a
# guard that fails open the moment one is added.
WRITE_BACKENDS = {
    "call_service": REST,
    "upsert_automation": REST,
    "upsert_script": REST,
    "upsert_scene": REST,
    "upsert_helper": WS,
    "delete_automation": REST,
    "delete_script": REST,
    "delete_scene": REST,
    "reload": REST,
}

BACKENDS = {**READ_BACKENDS, **WRITE_BACKENDS}

READ_ACTIONS = frozenset(READ_BACKENDS)
WRITE_ACTIONS = frozenset(WRITE_BACKENDS)
ALL_ACTIONS = frozenset(BACKENDS)


def backend_for(action: str) -> str:
    """Returns the transport that serves `action`.

    Raises KeyError for anything unknown: a typo should fail here, loudly, rather
    than fall through to a default backend that then returns a confusing 404.
    """
    return BACKENDS[action]


def is_write(action: str) -> bool:
    return action in WRITE_ACTIONS
