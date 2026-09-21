"""A deliberately small Model Context Protocol client.

Home Assistant's MCP server speaks the Streamable HTTP transport in its stateless
mode: one HTTP POST carrying a JSON-RPC request, one response. Measured against
the live instance, `resources/read` works from a cold client with no
`initialize` handshake, no `mcp-session-id`, and no open stream -- 0.14s for a
15KB snapshot.

That is why this is plain synchronous `httpx` rather than the `mcp` SDK. The SDK
is built around an async session with a task group and a persistent stream,
which would have to be bridged back into the synchronous tool layer with
`asyncio.run()`. That bridge is not merely ugly -- it raises when a loop is
already running, which is exactly the situation inside the Discord runner. The
same reasoning produced a synchronous `ws.py`; see the note at the top of that
module.

## What this client refuses to do

`tools/call` is not implemented, and `ALLOWED_METHODS` does not contain it.

This is a safety boundary, not an oversight. The reference instance exposes 25
MCP tools, and they are not all read-only:

    HassTurnOn   "... For locks, this performs a 'lock' action."
    HassTurnOff  "... For locks, this performs an 'unlock' action."

`lock` is in `guards.BLOCKED_DOMAINS`. A client that could call MCP tools would
therefore be a complete bypass of the entire write path -- the `HA_WRITE_ENABLED`
kill switch, the blocked-domain list, and the confirmation protocol all live in
`guards.py`, on the REST side. An agent could unlock a door by asking a
*different transport* to do it.

Restricting the transport is a stronger guarantee than allow-listing individual
tool names, because there is no code path to widen. Adding `tools/call` here
would require writing the method, which is a change a reviewer will see.
"""
import json
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# Sent as the client's protocol version. The server echoes its own; we do not
# negotiate, because every method used here is stable across the versions that
# have an `/api/mcp` endpoint at all.
PROTOCOL_VERSION = "2025-06-18"

DEFAULT_TIMEOUT = 30

# Read-only introspection and resource reads. See the module docstring for why
# `tools/call` is absent and must stay absent.
ALLOWED_METHODS = frozenset({
    "initialize",
    "ping",
    "resources/list",
    "resources/read",
    "prompts/list",
    "prompts/get",
    "tools/list",
})


class MCPError(Exception):
    """The server was reached and refused, or replied with something unusable."""


class MCPUnavailable(MCPError):
    """The server could not be reached at all.

    Separated from `MCPError` because the two call for different handling: an
    unreachable server is a temporary condition an agent should degrade around,
    while a rejected request usually means a misconfiguration that will not fix
    itself. Callers that want to degrade gracefully catch this one specifically.
    """


class MCPClient:
    """One MCP endpoint, addressed one request at a time.

    Server-agnostic: it knows the protocol, not who is answering. Anything
    specific to Home Assistant -- the token file, the base URL, which resource
    holds the snapshot -- belongs to the caller.
    """

    def __init__(
        self,
        endpoint: str,
        token: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        client_name: str = "aoc",
        transport=None,
    ):
        self.endpoint = endpoint
        self.token = token
        self.timeout = timeout
        self.client_name = client_name
        # Injection seam for tests, so they exercise this module's request
        # building and response parsing rather than a hand-rolled substitute.
        self._transport = transport

    def __repr__(self) -> str:
        return f"MCPClient(endpoint={self.endpoint!r})"

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            # Both are advertised because the transport permits the server to
            # answer either way. Home Assistant currently returns plain JSON,
            # but `_payload` handles the streamed framing too, so an upgrade
            # that starts streaming will not read as a parse failure.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Issues one JSON-RPC request and returns its `result`.

        Raises `MCPUnavailable` if the endpoint could not be reached, and
        `MCPError` for anything the server rejected or that could not be parsed.

        One attempt, deliberately. `client.py` retries connection failures three
        times because a REST read is often the only way to get an answer. Here
        there is always another way -- `inventory` and `search_registry` reach
        the same instance over different transports -- and the timeout is 20
        seconds, measured against a real outage. Retrying would turn a 20-second
        failure into a 60-second stall before the agent is told to try the route
        that might have worked.
        """
        if method not in ALLOWED_METHODS:
            # Deliberately phrased as a refusal rather than "unknown method":
            # the method may well exist on the server. We are declining to speak
            # it. `tools/call` is the case that matters.
            raise MCPError(
                f"This client does not issue '{method}'. Permitted methods: "
                f"{', '.join(sorted(ALLOWED_METHODS))}."
            )

        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}

        try:
            response = self._post(body)
        except httpx.RequestError as exc:
            raise MCPUnavailable(
                f"Could not reach the MCP endpoint at {self.endpoint}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        if response.status_code == 401:
            raise MCPError(
                f"MCP endpoint {self.endpoint} rejected the token (401). The token may "
                f"have been revoked or rotated."
            )
        if response.status_code == 404:
            raise MCPError(
                f"No MCP endpoint at {self.endpoint} (404). The MCP Server integration "
                f"is probably not configured on this Home Assistant instance."
            )
        if response.status_code >= 400:
            raise MCPError(
                f"MCP endpoint {self.endpoint} returned HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )

        payload = self._payload(response)

        # A JSON-RPC error arrives with HTTP 200 and an `error` member. Checking
        # the status code alone reads that as success and then fails further
        # downstream on a missing `result`, where the real message is long gone.
        # Verified against the live server: an unknown resource URI returns
        # 200 with {"error": {"code": 0, "message": "Unknown resource: ..."}}.
        if isinstance(payload, dict) and "error" in payload:
            error = payload["error"] or {}
            raise MCPError(
                f"MCP call '{method}' failed: "
                f"{error.get('message', 'no message')} (code {error.get('code', 'none')})"
            )

        if not isinstance(payload, dict) or "result" not in payload:
            raise MCPError(f"MCP call '{method}' returned no result: {str(payload)[:200]}")

        return payload["result"]

    def _post(self, body: Dict[str, Any]):
        if self._transport is not None:
            return self._transport(self.endpoint, self._headers(), body, self.timeout)
        return httpx.post(
            self.endpoint, headers=self._headers(), json=body, timeout=self.timeout
        )

    def _payload(self, response) -> Any:
        """Decodes a response body that may be JSON or an SSE frame."""
        text = response.text or ""
        content_type = (response.headers.get("content-type") or "").lower()

        if "text/event-stream" in content_type:
            text = _last_sse_data(text)

        try:
            return json.loads(text)
        except ValueError as exc:
            raise MCPError(
                f"MCP endpoint returned a body that is not JSON ({content_type}): "
                f"{text[:200]}"
            ) from exc


def _last_sse_data(stream: str) -> str:
    """Pulls the final `data:` payload out of an SSE response body.

    The server may answer a single request with a short event stream. The reply
    is the last data frame; earlier frames are progress notifications, which
    carry no result and would parse into something without a `result` member.
    """
    frames = [
        line[len("data:"):].strip()
        for line in stream.splitlines()
        if line.startswith("data:")
    ]
    return frames[-1] if frames else stream
