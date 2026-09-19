"""Tests for the WebSocket registry client.

A fake connection stands in for the socket, so these cover the parts that are
easy to get wrong -- the auth handshake, matching replies to their request id,
and not mistaking an interleaved event for an answer -- without any network.
"""
import json
import unittest

from core.integrations.homeassistant import ws as ws_mod
from core.integrations.homeassistant.client import HomeAssistantError


class FakeConnection:
    """Replays queued frames and records what was sent."""

    def __init__(self, frames):
        self.frames = [json.dumps(f) if isinstance(f, dict) else f for f in frames]
        self.sent = []
        self.closed = False

    def send(self, raw):
        self.sent.append(json.loads(raw))

    def recv(self):
        if not self.frames:
            raise AssertionError("recv() called with no frames left")
        return self.frames.pop(0)

    def close(self):
        self.closed = True


AUTH_OK = [{"type": "auth_required", "ha_version": "2026.6.4"}, {"type": "auth_ok"}]


def connected(extra_frames=()):
    conn = FakeConnection(list(AUTH_OK) + list(extra_frames))
    client = ws_mod.HomeAssistantWebSocket(
        base_url="https://ha.example.com", token="sekret", connection=conn
    )
    client.connect()
    return client, conn


class TestUrl(unittest.TestCase):
    def test_https_becomes_wss(self):
        self.assertEqual(
            ws_mod._ws_url("https://ha.example.com"), "wss://ha.example.com/api/websocket"
        )

    def test_http_becomes_ws(self):
        self.assertEqual(
            ws_mod._ws_url("http://ha.local:8123"), "ws://ha.local:8123/api/websocket"
        )

    def test_unknown_scheme_is_rejected(self):
        with self.assertRaises(HomeAssistantError):
            ws_mod._ws_url("ha.example.com")


class TestHandshake(unittest.TestCase):
    def test_sends_the_token_after_auth_required(self):
        _, conn = connected()
        self.assertEqual(conn.sent[0], {"type": "auth", "access_token": "sekret"})

    def test_auth_invalid_is_reported_without_echoing_the_token(self):
        conn = FakeConnection([
            {"type": "auth_required"},
            {"type": "auth_invalid", "message": "Invalid access token sekret"},
        ])
        client = ws_mod.HomeAssistantWebSocket(
            base_url="https://ha.example.com", token="sekret", connection=conn
        )
        with self.assertRaises(HomeAssistantError) as ctx:
            client.connect()
        self.assertIn("rejected the token", str(ctx.exception))
        # HA's own message contained the credential; it must not be forwarded.
        self.assertNotIn("sekret", str(ctx.exception))

    def test_unexpected_greeting_is_rejected(self):
        conn = FakeConnection([{"type": "result"}])
        client = ws_mod.HomeAssistantWebSocket(
            base_url="https://ha.example.com", token="t", connection=conn
        )
        with self.assertRaises(HomeAssistantError) as ctx:
            client.connect()
        self.assertIn("Unexpected greeting", str(ctx.exception))

    def test_repr_never_contains_the_token(self):
        client, _ = connected()
        self.assertNotIn("sekret", repr(client))


class TestCommand(unittest.TestCase):
    def test_returns_the_result_and_increments_ids(self):
        client, conn = connected([
            {"id": 1, "success": True, "result": [{"area_id": "garage"}]},
            {"id": 2, "success": True, "result": []},
        ])
        self.assertEqual(client.command("config/area_registry/list"), [{"area_id": "garage"}])
        self.assertEqual(client.command("config/label_registry/list"), [])
        self.assertEqual([m["id"] for m in conn.sent if "id" in m], [1, 2])

    def test_skips_frames_that_are_not_the_reply(self):
        """HA interleaves events on the same socket; matching on id is required."""
        client, _ = connected([
            {"type": "event", "event": {"data": "noise"}},
            {"id": 99, "success": True, "result": "someone else's reply"},
            {"id": 1, "success": True, "result": "mine"},
        ])
        self.assertEqual(client.command("config/area_registry/list"), "mine")

    def test_failure_raises_with_the_error_code(self):
        client, _ = connected([
            {"id": 1, "success": False, "error": {"code": "unknown_command", "message": "nope"}},
        ])
        with self.assertRaises(HomeAssistantError) as ctx:
            client.command("config/bogus/list")
        self.assertIn("unknown_command", str(ctx.exception))

    def test_command_before_connect_is_refused(self):
        client = ws_mod.HomeAssistantWebSocket(base_url="https://ha.example.com", token="t")
        with self.assertRaises(HomeAssistantError) as ctx:
            client.command("config/area_registry/list")
        self.assertIn("not connected", str(ctx.exception))

    def test_non_json_frame_is_reported(self):
        client, _ = connected(["this is not json"])
        with self.assertRaises(HomeAssistantError) as ctx:
            client.command("config/area_registry/list")
        self.assertIn("non-JSON", str(ctx.exception))


class TestHelpersAndLifecycle(unittest.TestCase):
    def test_named_helpers_issue_the_right_commands(self):
        client, conn = connected([
            {"id": 1, "success": True, "result": []},
            {"id": 2, "success": True, "result": []},
            {"id": 3, "success": True, "result": []},
            {"id": 4, "success": True, "result": []},
        ])
        client.areas()
        client.devices()
        client.entities()
        client.labels()
        types = [m.get("type") for m in conn.sent if m.get("type") != "auth"]
        self.assertEqual(types, [
            "config/area_registry/list",
            "config/device_registry/list",
            "config/entity_registry/list",
            "config/label_registry/list",
        ])

    def test_context_manager_closes_only_what_it_opened(self):
        # An injected connection is the caller's to close.
        conn = FakeConnection(list(AUTH_OK))
        with ws_mod.HomeAssistantWebSocket(
            base_url="https://ha.example.com", token="t", connection=conn
        ):
            pass
        self.assertFalse(conn.closed)


if __name__ == "__main__":
    unittest.main()
