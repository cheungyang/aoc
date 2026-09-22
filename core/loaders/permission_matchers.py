"""How a permission selector is matched against the thing being acted on.

`check_permission` was written for the filesystem, where a grant key is a
directory and "does this apply?" means "is the target inside it?". That is the
right question for `filesystem` and `bash`, and it is resolved with real path
arithmetic -- `os.path.abspath`, prefix comparison, the lot.

It is the wrong question for a tool whose targets are not paths. Home Assistant
targets are entity ids like `light.porch`, and run through the path matcher they
behave in ways nobody would predict from reading the grant:

    {"light.*": [...]}   matches nothing at all -- `*` is a literal character in
                         a path, not a wildcard, so this grant is dead.
    {"*": [...]}         matches only the literal string `"*"`, which the tool
                         happens to pass when an instruction names no entity. So
                         it permits `inventory` but denies `get_state` on
                         `light.porch` -- the opposite of what it reads like.

Both were measured, not inferred. A grant that silently denies is the failure
mode this module exists to prevent; the more dangerous sibling -- a grant that
silently *allows* -- is why the fix is opt-in rather than a change to the
default. Tools that do not declare a matcher keep path semantics exactly.

A tool opts in by exporting `PERMISSION_MATCHER`: a callable taking
`(selector, target)` and returning whether the selector covers the target.
"""
import logging

from core.loaders.tool_declarations import declaration

logger = logging.getLogger(__name__)

MATCHER_ATTR = "PERMISSION_MATCHER"


def matcher_for(tool_id: str):
    """Returns a tool's selector matcher, or None to use path semantics.

    `None` is the safe default: it means a tool whose module cannot be imported
    falls back to path matching, which for a non-path tool denies nearly
    everything. A broken tool therefore locks down rather than opens up.
    """
    matcher = declaration(tool_id, MATCHER_ATTR, None)
    return matcher if callable(matcher) else None


def matches(matcher, selector: str, target: str) -> bool:
    """Applies a matcher, treating any failure as no-match.

    A matcher that raises must not propagate into `check_permission`: the caller
    is deciding whether to permit an action, and an exception there would either
    crash the tool call or, if caught carelessly upstream, be mistaken for a
    verdict. Denying is the only safe reading of "the matcher did not work".
    """
    try:
        return bool(matcher(selector, target))
    except Exception as exc:
        logger.warning(
            "Permission matcher raised on selector=%r target=%r (%s: %s); "
            "treating as no match.", selector, target, type(exc).__name__, exc,
        )
        return False
