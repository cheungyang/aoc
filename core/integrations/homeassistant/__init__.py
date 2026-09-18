"""Home Assistant integration: transport and credentials.

Deliberately exports the client and its error type only. Actions, permissions and
the agent-facing tool live above this layer.
"""
from core.integrations.homeassistant.client import (
    HomeAssistantClient,
    HomeAssistantError,
    read_token,
    redact,
    resolve_token_path,
    write_enabled,
)

__all__ = [
    "HomeAssistantClient",
    "HomeAssistantError",
    "read_token",
    "redact",
    "resolve_token_path",
    "write_enabled",
]
