"""WebSocket access to Home Assistant's registries.

The REST API exposes state; the registries -- which entities exist, what they are
called, which device and area they belong to, whether they are disabled -- are
only reachable over the WebSocket API. That gap is the entire reason this module
exists, and it is why the numbers differ: on the reference instance REST reports
920 states while the entity registry holds 1404 entries. The difference is
disabled, hidden and currently-unavailable entities, which an inventory needs to
know about and a state dump silently omits.

Connections are short-lived by design. Every call opens a socket, authenticates,
issues its commands and closes. There are no subscriptions here, so a pooled or
long-lived connection would buy nothing and cost a reconnect state machine. If
Phase 4+ ever needs event streaming, that is the point to revisit this.

Synchronous, via `websocket-client`. See the note in requirements.txt.
"""
import json
from typing import Any, Dict, List, Optional

import websocket

from core.integrations.homeassistant.client import (
    DEFAULT_TIMEOUT,
    BASE_URL_ENV_VAR,
    HomeAssistantError,
    read_token,
    redact,
)
from core.util.config import Config


def _ws_url(base_url: str) -> str:
    """https://host -> wss://host/api/websocket (and http -> ws)."""
    if base_url.startswith("https://"):
        scheme = "wss://"
    elif base_url.startswith("http://"):
        scheme = "ws://"
    else:
        raise HomeAssistantError(
            f"{BASE_URL_ENV_VAR} must start with http:// or https://, got: {base_url}"
        )
    host = base_url.split("://", 1)[1].rstrip("/")
    return f"{scheme}{host}/api/websocket"


class HomeAssistantWebSocket:
    """One short-lived, authenticated WebSocket conversation.

    Used as a context manager:

        with HomeAssistantWebSocket() as ws:
            areas = ws.command("config/area_registry/list")
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: Optional[int] = None,
        token_path: Optional[str] = None,
        connection=None,
    ):
        resolved_base = (base_url or Config().get(BASE_URL_ENV_VAR) or "").strip()
        if not resolved_base:
            raise HomeAssistantError(
                f"{BASE_URL_ENV_VAR} is not set. Add it to .env, e.g. "
                f"{BASE_URL_ENV_VAR}=https://homeassistant.example.com"
            )
        self.base_url = resolved_base.rstrip("/")
        self.url = _ws_url(self.base_url)

        self._token = token if token is not None else read_token(token_path)
        self.timeout = int(timeout if timeout is not None else Config().get("HA_REQUEST_TIMEOUT", DEFAULT_TIMEOUT))

        # Injectable so tests never open a socket.
        self._connection = connection
        self._owns_connection = connection is None
        self._message_id = 0

    # -- Lifecycle -----------------------------------------------------------

    def __enter__(self) -> "HomeAssistantWebSocket":
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def __repr__(self) -> str:
        """Loggable. Never includes the token."""
        return f"<HomeAssistantWebSocket {self.url} timeout={self.timeout}s>"

    def connect(self) -> None:
        """Opens the socket and completes HA's auth handshake.

        HA speaks first with `auth_required`, then expects `auth`, then answers
        `auth_ok` or `auth_invalid`. Nothing else may be sent until that is done.
        """
        if self._connection is None:
            try:
                self._connection = websocket.create_connection(self.url, timeout=self.timeout)
            except Exception as exc:
                raise HomeAssistantError(
                    f"Could not open a WebSocket to {self.url}: {redact(exc, self._token)}. "
                    f"If Home Assistant sits behind a reverse proxy, that proxy must be "
                    f"configured to forward WebSocket upgrade headers."
                ) from None

        greeting = self._receive()
        if greeting.get("type") != "auth_required":
            raise HomeAssistantError(
                f"Unexpected greeting from {self.url}: {redact(greeting.get('type'), self._token)}"
            )

        self._send({"type": "auth", "access_token": self._token})
        result = self._receive()

        if result.get("type") != "auth_ok":
            # The message from HA can echo the credential, so it is not forwarded.
            raise HomeAssistantError(
                f"Home Assistant rejected the token over WebSocket ({result.get('type')}). "
                f"The token may be expired or revoked; issue a new one under Profile > Security."
            )

    def close(self) -> None:
        if self._connection is not None and self._owns_connection:
            try:
                self._connection.close()
            except Exception:
                # Nothing actionable: the conversation is over either way, and an
                # error closing must not mask the caller's real result.
                pass
        self._connection = None

    # -- Messaging -----------------------------------------------------------

    def _send(self, payload: Dict[str, Any]) -> None:
        self._connection.send(json.dumps(payload))

    def _receive(self) -> Dict[str, Any]:
        try:
            raw = self._connection.recv()
        except Exception as exc:
            raise HomeAssistantError(
                f"WebSocket read from {self.url} failed: {redact(exc, self._token)}"
            ) from None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            raise HomeAssistantError(
                f"WebSocket sent a non-JSON frame: {redact(raw, self._token)[:200]}"
            ) from None

    def command(self, command_type: str, **params: Any) -> Any:
        """Issues one command and returns its `result`.

        Each command carries a monotonically increasing id, and replies are
        matched on it. Frames that are not the reply -- HA interleaves events and
        other traffic on the same socket -- are skipped rather than mistaken for
        the answer, which is the bug this loop exists to prevent.
        """
        if self._connection is None:
            raise HomeAssistantError("WebSocket is not connected; use it as a context manager.")

        self._message_id += 1
        message_id = self._message_id
        self._send({"id": message_id, "type": command_type, **params})

        while True:
            message = self._receive()
            if message.get("id") != message_id:
                continue
            if message.get("success"):
                return message.get("result")
            error = message.get("error") or {}
            raise HomeAssistantError(
                f"Home Assistant refused '{command_type}': "
                f"{error.get('code', 'unknown')} {error.get('message', '')}".strip()
            )

    # -- Registry helpers ----------------------------------------------------
    #
    # Thin named wrappers rather than raw strings at the call site: the command
    # names are an external API that has changed before, and this keeps the churn
    # in one place.

    def areas(self) -> List[Dict[str, Any]]:
        return self.command("config/area_registry/list")

    def devices(self) -> List[Dict[str, Any]]:
        return self.command("config/device_registry/list")

    def entities(self) -> List[Dict[str, Any]]:
        return self.command("config/entity_registry/list")

    def labels(self) -> List[Dict[str, Any]]:
        return self.command("config/label_registry/list")
