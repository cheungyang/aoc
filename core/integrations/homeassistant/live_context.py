"""The `live_context` action: Home Assistant's own summary of the home.

Home Assistant already composes a natural-language picture of every entity it
has been told to expose to voice assistants -- names, domains, current states,
the attributes that matter. Reconstructing that from the registries would mean
re-deriving a judgment HA has already made about which entities are worth
mentioning, and would drift from it on every upgrade. So this reads HA's version.

It complements `inventory` rather than replacing it:

  * `inventory` covers everything that exists -- 1407 registry entries on the
    reference instance, including disabled and hidden ones -- as a compact
    area x domain table.
  * `live_context` covers only what is *exposed* to Assist -- 163 entries there --
    but with current state for each, in prose HA wrote itself.

Read-only by construction. The snapshot arrives as an MCP *resource*, and the
client used here cannot issue `tools/call` at all; see the docstring of
`core/integrations/mcp/client_manager.py` for why that boundary exists and what
it is protecting.
"""
from typing import Optional, Tuple

from core.integrations.homeassistant.client import (
    BASE_URL_ENV_VAR,
    DEFAULT_TIMEOUT,
    TIMEOUT_ENV_VAR,
    HomeAssistantError,
    read_token,
)
from core.integrations.mcp.client_manager import MCPClient, MCPError, MCPUnavailable
from core.util.config import Config

# Home Assistant serves MCP here. The older `/mcp_server/sse` path still answers
# on current versions but expects a GET and the SSE transport; `/api/mcp` is the
# stateless Streamable HTTP endpoint and is what the documentation now points at.
MCP_PATH = "/api/mcp"

# The read-only resource mirroring the `GetLiveContext` tool's output. Preferred
# over the tool precisely because reading a resource cannot actuate anything.
SNAPSHOT_URI = "homeassistant://assist/context-snapshot"

# Entries start at column zero; everything below them is indented. This is the
# seam the snapshot is cut on so a truncated response is never half an entry.
ENTRY_PREFIX = "- names:"

# The live snapshot is ~15,000 characters, roughly 3,800 tokens -- too much to
# drop into a batch response by default, and most of it is irrelevant to any one
# question. 8,000 keeps a typical reply near 2,000 tokens while still showing
# about half the home; `search_registry` is the right tool for anything specific.
DEFAULT_MAX_CHARS = 8000

# A caller may raise the budget, but not without limit: an agent that asks for
# everything on a much larger installation should still not be able to exhaust
# the context window in one instruction.
HARD_MAX_CHARS = 20000
MIN_MAX_CHARS = 500


def endpoint(base_url: Optional[str] = None) -> str:
    resolved = (base_url or Config().get(BASE_URL_ENV_VAR) or "").strip()
    if not resolved:
        raise HomeAssistantError(
            f"{BASE_URL_ENV_VAR} is not set. Add it to .env, e.g. "
            f"{BASE_URL_ENV_VAR}=https://homeassistant.example.com"
        )
    return resolved.rstrip("/") + MCP_PATH


def build_client(
    base_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout: Optional[int] = None,
    token_path: Optional[str] = None,
) -> MCPClient:
    """Constructs a client pointed at this instance's MCP endpoint."""
    resolved_timeout = timeout
    if resolved_timeout is None:
        try:
            resolved_timeout = int(Config().get(TIMEOUT_ENV_VAR) or DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            resolved_timeout = DEFAULT_TIMEOUT

    return MCPClient(
        endpoint=endpoint(base_url),
        token=token if token is not None else read_token(token_path),
        timeout=resolved_timeout,
        client_name="aoc-home-assistant",
    )


def _clamp(max_chars: Optional[int]) -> int:
    if max_chars is None:
        return DEFAULT_MAX_CHARS
    try:
        value = int(max_chars)
    except (TypeError, ValueError):
        return DEFAULT_MAX_CHARS
    return max(MIN_MAX_CHARS, min(HARD_MAX_CHARS, value))


def truncate(text: str, max_chars: int) -> Tuple[str, int, int]:
    """Cuts the snapshot to a budget on an entry boundary.

    Returns `(text, shown, total)`. `shown` and `total` count entries, so the
    caller can say how much was withheld instead of leaving the model to guess
    whether it is looking at the whole home.

    Cutting mid-entry would produce a fragment that reads like a complete record
    with fields missing -- worse than omitting it, because a model has no way to
    tell a truncated entry from an entity that genuinely has no state.
    """
    lines = text.split("\n")
    starts = [i for i, line in enumerate(lines) if line.startswith(ENTRY_PREFIX)]
    total = len(starts)

    if len(text) <= max_chars or not starts:
        return text, total, total

    kept = lines[: starts[0]]
    shown = 0
    length = len("\n".join(kept))

    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        block = lines[start:end]
        block_length = len("\n".join(block)) + 1
        if length + block_length > max_chars:
            break
        kept.extend(block)
        length += block_length
        shown += 1

    return "\n".join(kept).rstrip(), shown, total


def snapshot(
    max_chars: Optional[int] = None,
    client: Optional[MCPClient] = None,
) -> Tuple[str, int, int]:
    """Fetches the live context snapshot. Returns `(text, shown, total)`.

    Raises `MCPUnavailable` when Home Assistant cannot be reached and `MCPError`
    when it answers but refuses. Both are left to the caller: whether an
    unreachable instance is a hard failure or one degraded line in a batch
    response is a decision for the tool layer, not for this function.
    """
    mcp = client or build_client()
    result = mcp.call("resources/read", {"uri": SNAPSHOT_URI})

    contents = (result or {}).get("contents") or []
    if not contents:
        raise MCPError(
            f"The MCP server returned no content for {SNAPSHOT_URI}. The Assist API "
            f"may not expose GetLiveContext on this instance."
        )

    text = contents[0].get("text") or ""
    if not text.strip():
        raise MCPError(f"The snapshot at {SNAPSHOT_URI} was empty.")

    return truncate(text, _clamp(max_chars))
