"""Reading declarations that tool modules make about themselves.

Some facts about a tool belong next to the tool: which action names group into
`@write`, how a permission selector is matched against a target. Keeping them in
a central registry means editing two files every time a tool changes, and the
copy that lives away from the code is the one that goes stale.

This module is the shared mechanism for finding those declarations. It exists so
that `permission_bundles` and `permission_matchers` cannot drift apart in how
they handle a tool that fails to import -- which is a security-relevant
behaviour, not a detail. Both must fail closed, and they do so here once.
"""
import importlib
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# (tool_id, attribute) -> value. A tool module is imported at most once per
# process for this purpose.
_cache: Dict[Tuple[str, str], Any] = {}


def _module_path(tool_id: str) -> str:
    """Resolves `tool_id` to its module path using the loader's own discovery.

    Imported lazily: `tools.home_assistant` imports `ToolsLoader`, so a
    top-level import here would close a cycle through the loader that imports
    this module.
    """
    from core.loaders.tools_loader import ToolsLoader

    folder = ToolsLoader()._discover_tools().get(tool_id)
    return f"tools.{folder}.{tool_id}" if folder else f"tools.{tool_id}"


def declaration(tool_id: str, attribute: str, default: Any = None) -> Any:
    """Returns `attribute` from a tool's module, or `default`.

    A tool that fails to import yields the default rather than raising. Callers
    must choose a default that denies: a broken tool module should narrow what
    is permitted, never widen it.

    The failure is logged rather than swallowed outright. Failing closed is
    correct, but the symptom it produces -- an agent inexplicably denied
    everything it was granted -- points at the permission system, not at the
    broken tool that actually caused it.
    """
    key = (tool_id, attribute)
    if key in _cache:
        return _cache[key]

    value = default
    try:
        module = importlib.import_module(_module_path(tool_id))
        value = getattr(module, attribute, default)
    except Exception as exc:
        logger.warning(
            "Could not read '%s' from tool '%s' (%s: %s). Falling back to the "
            "default, which denies rather than grants.",
            attribute, tool_id, type(exc).__name__, exc,
        )
        value = default

    _cache[key] = value
    return value


def clear_cache(tool_id: Optional[str] = None) -> None:
    """Forgets discovered declarations. For tests and hot reload."""
    if tool_id is None:
        _cache.clear()
        return
    for key in [k for k in _cache if k[0] == tool_id]:
        del _cache[key]
