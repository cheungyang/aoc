"""Tests for the MCP client.

Weighted towards two things that unit tests are unusually good at pinning:

  * the `tools/call` refusal, which is a safety boundary rather than a feature,
    and which no live probe would ever exercise because the correct behaviour is
    that the request is never made;
  * the JSON-RPC-error-inside-HTTP-200 case, which is easy to get wrong and
    produces a confusing downstream failure when you do.
"""
import json
import unittest

import httpx

from core.integrations.mcp.client_manager import (
    ALLOWED_METHODS,
    MCPClient,
    MCPError,
    MCPUnavailable,
)


class FakeResponse:
    def __init__(self, status_code=200, body="", content_type="application/json"):
        self.status_code = status_code
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.headers = {"content-type": content_type}


def transport_returning(response, record=None):
    def _transport(endpoint, headers, body, timeout):
        if record is not None:
            record.append({"endpoint": endpoint, "headers": headers, "body": body,
                           "timeout": timeout})
        if isinstance(response, Exception):
            raise response
        return response
    return _transport


def ok(result):
    return FakeResponse(body={"jsonrpc": "2.0", "id": 1, "result": result})


class TestRefusesToActuate(unittest.TestCase):
    """The client must not be able to invoke Home Assistant's MCP tools.

    HA exposes 25 tools over MCP, including HassTurnOff, whose own description
    says it performs an 'unlock' action on locks. `lock` is a blocked domain in
    guards.py, and none of the write protections -- kill switch, deny-list,
    confirmation -- exist on the MCP plane. A client that could call tools would
    route straight around all of them.
    """

    def test_tools_call_is_not_allowed(self):
        self.assertNotIn("tools/call", ALLOWED_METHODS)

    def test_calling_a_tool_raises_without_sending_anything(self):
        sent = []
        client = MCPClient("http://ha/api/mcp", transport=transport_returning(ok({}), sent))

        with self.assertRaises(MCPError):
            client.call("tools/call", {"name": "HassTurnOff", "arguments": {}})

        # The point is not just that it raises -- it is that no request was made.
        self.assertEqual(sent, [], "a refused method must not reach the network")

    def test_the_refusal_names_the_permitted_methods(self):
        client = MCPClient("http://ha/api/mcp", transport=transport_returning(ok({})))

        with self.assertRaises(MCPError) as caught:
            client.call("tools/call")

        self.assertIn("resources/read", str(caught.exception))

    def test_no_allowed_method_can_mutate(self):
        """A guard on the allowlist itself, so the set cannot quietly grow.

        Anything ending in /call, or any write-shaped verb, would be a new
        capability rather than a new read.
        """
        for method in ALLOWED_METHODS:
            self.assertFalse(
                method.endswith("/call") or method.startswith(("tools/call", "sampling")),
                f"'{method}' is not a read-only method",
            )


class TestRequestShape(unittest.TestCase):
    def test_sends_jsonrpc_envelope(self):
        sent = []
        client = MCPClient("http://ha/api/mcp", transport=transport_returning(ok({}), sent))
        client.call("resources/list")

        body = sent[0]["body"]
        self.assertEqual(body["jsonrpc"], "2.0")
        self.assertEqual(body["method"], "resources/list")
        self.assertEqual(body["params"], {})

    def test_sends_the_bearer_token(self):
        sent = []
        client = MCPClient("http://ha/api/mcp", token="sekrit",
                           transport=transport_returning(ok({}), sent))
        client.call("resources/list")

        self.assertEqual(sent[0]["headers"]["Authorization"], "Bearer sekrit")

    def test_omits_authorization_when_there_is_no_token(self):
        sent = []
        client = MCPClient("http://ha/api/mcp", transport=transport_returning(ok({}), sent))
        client.call("resources/list")

        self.assertNotIn("Authorization", sent[0]["headers"])

    def test_accepts_both_response_encodings(self):
        """The transport permits either; advertising one invites a 406."""
        sent = []
        client = MCPClient("http://ha/api/mcp", transport=transport_returning(ok({}), sent))
        client.call("resources/list")

        accept = sent[0]["headers"]["Accept"]
        self.assertIn("application/json", accept)
        self.assertIn("text/event-stream", accept)


class TestErrorHandling(unittest.TestCase):
    def test_a_jsonrpc_error_raises_even_though_http_was_200(self):
        """The case the live server actually produces for a bad resource URI.

        Checking the status code alone treats this as success, then fails later
        on a missing 'result' with the real message already discarded.
        """
        body = {"jsonrpc": "2.0", "id": 1,
                "error": {"code": 0, "message": "Unknown resource: homeassistant://nope"}}
        client = MCPClient("http://ha/api/mcp",
                           transport=transport_returning(FakeResponse(200, body)))

        with self.assertRaises(MCPError) as caught:
            client.call("resources/read", {"uri": "homeassistant://nope"})

        self.assertIn("Unknown resource", str(caught.exception))

    def test_a_connection_failure_is_unavailable_not_error(self):
        """Callers degrade on MCPUnavailable; the distinction has to hold."""
        client = MCPClient(
            "http://ha/api/mcp",
            transport=transport_returning(httpx.ConnectError("no route to host")),
        )

        with self.assertRaises(MCPUnavailable):
            client.call("resources/list")

    def test_unavailable_is_catchable_as_mcp_error(self):
        """Code that does not care about the distinction should not have to."""
        self.assertTrue(issubclass(MCPUnavailable, MCPError))

    def test_401_explains_the_token(self):
        client = MCPClient("http://ha/api/mcp",
                           transport=transport_returning(FakeResponse(401, "401: Unauthorized",
                                                                      "text/plain")))
        with self.assertRaises(MCPError) as caught:
            client.call("resources/list")

        self.assertIn("token", str(caught.exception).lower())

    def test_404_explains_the_missing_integration(self):
        """The most likely real cause, and not obvious from '404'."""
        client = MCPClient("http://ha/api/mcp",
                           transport=transport_returning(FakeResponse(404, "404: Not Found",
                                                                      "text/plain")))
        with self.assertRaises(MCPError) as caught:
            client.call("resources/list")

        self.assertIn("integration", str(caught.exception).lower())

    def test_a_non_json_body_is_reported_as_such(self):
        client = MCPClient("http://ha/api/mcp",
                           transport=transport_returning(FakeResponse(200, "<html>nope</html>")))
        with self.assertRaises(MCPError) as caught:
            client.call("resources/list")

        self.assertIn("not JSON", str(caught.exception))

    def test_a_response_without_a_result_is_rejected(self):
        client = MCPClient("http://ha/api/mcp",
                           transport=transport_returning(FakeResponse(200, {"jsonrpc": "2.0"})))
        with self.assertRaises(MCPError):
            client.call("resources/list")


class TestStreamedResponses(unittest.TestCase):
    """Home Assistant answers with plain JSON today, but the transport allows
    an event stream. Handling it costs a few lines and avoids a parse error that
    would look like a protocol break after an upgrade.
    """

    def test_reads_an_sse_framed_reply(self):
        stream = (
            "event: message\n"
            'data: {"jsonrpc":"2.0","id":1,"result":{"contents":[{"text":"hi"}]}}\n'
            "\n"
        )
        client = MCPClient(
            "http://ha/api/mcp",
            transport=transport_returning(FakeResponse(200, stream, "text/event-stream")),
        )

        result = client.call("resources/read", {"uri": "x"})
        self.assertEqual(result["contents"][0]["text"], "hi")

    def test_takes_the_last_frame_when_several_arrive(self):
        """Earlier frames are progress notifications and carry no result."""
        stream = (
            'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n'
            "\n"
            'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n'
        )
        client = MCPClient(
            "http://ha/api/mcp",
            transport=transport_returning(FakeResponse(200, stream, "text/event-stream")),
        )

        self.assertEqual(client.call("resources/read", {"uri": "x"}), {"ok": True})


if __name__ == "__main__":
    unittest.main()
