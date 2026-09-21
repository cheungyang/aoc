"""Tests for the live_context snapshot.

Most of the weight is on truncation, because that is where a plausible-looking
bug does real damage: a snapshot cut mid-entry reads as a complete record with
missing fields, and a model has no way to tell that apart from an entity that
genuinely has no state.
"""
import unittest

from core.integrations.homeassistant import live_context as lc
from core.integrations.mcp.client_manager import MCPError


def snapshot_text(count, prefix="e"):
    """Builds a snapshot shaped like Home Assistant's real one."""
    lines = ["Live Context: An overview of the areas and the devices in this smart home:"]
    for index in range(count):
        lines.append(f"- names: {prefix}{index}")
        lines.append("  domain: light")
        lines.append("  state: 'on'")
    return "\n".join(lines)


class FakeClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def call(self, method, params=None):
        self.calls.append((method, params))
        if self.error:
            raise self.error
        return self.result


def resource(text):
    return {"contents": [{"uri": lc.SNAPSHOT_URI, "mimeType": "text/plain", "text": text}]}


class TestTruncation(unittest.TestCase):
    def test_short_snapshots_are_untouched(self):
        text = snapshot_text(3)
        out, shown, total = lc.truncate(text, 10000)

        self.assertEqual(out, text)
        self.assertEqual((shown, total), (3, 3))

    def test_counts_every_entry_even_when_cutting(self):
        """`total` must describe the home, not the part that survived."""
        _, shown, total = lc.truncate(snapshot_text(100), 500)

        self.assertEqual(total, 100)
        self.assertLess(shown, 100)

    def test_respects_the_budget(self):
        out, _, _ = lc.truncate(snapshot_text(200), 1000)
        self.assertLessEqual(len(out), 1000)

    def test_never_cuts_an_entry_in_half(self):
        """The property that matters. Every kept entry keeps all three fields."""
        out, shown, _ = lc.truncate(snapshot_text(200), 1000)

        blocks = [b for b in out.split(lc.ENTRY_PREFIX)[1:]]
        self.assertEqual(len(blocks), shown)
        for block in blocks:
            self.assertIn("domain:", block)
            self.assertIn("state:", block)

    def test_keeps_the_header(self):
        """Without it the payload is a bare list with no statement of what it is."""
        out, _, _ = lc.truncate(snapshot_text(200), 600)
        self.assertTrue(out.startswith("Live Context:"))

    def test_a_snapshot_with_no_entries_is_passed_through(self):
        out, shown, total = lc.truncate("Live Context: nothing is exposed.", 10)
        self.assertEqual(out, "Live Context: nothing is exposed.")
        self.assertEqual((shown, total), (0, 0))

    def test_a_single_oversized_entry_yields_a_header_not_a_fragment(self):
        """Better to return nothing than half a record."""
        text = "Live Context: header\n- names: x\n  attributes: " + ("y" * 5000)
        out, shown, total = lc.truncate(text, 600)

        self.assertEqual((shown, total), (0, 1))
        self.assertNotIn("yyyy", out)


class TestClamping(unittest.TestCase):
    def test_default_when_unset(self):
        self.assertEqual(lc._clamp(None), lc.DEFAULT_MAX_CHARS)

    def test_absurd_requests_are_capped(self):
        self.assertEqual(lc._clamp(10 ** 9), lc.HARD_MAX_CHARS)

    def test_tiny_requests_are_floored(self):
        self.assertEqual(lc._clamp(1), lc.MIN_MAX_CHARS)

    def test_garbage_falls_back_to_the_default(self):
        self.assertEqual(lc._clamp("lots"), lc.DEFAULT_MAX_CHARS)

    def test_a_reasonable_request_is_honoured(self):
        self.assertEqual(lc._clamp(3000), 3000)


class TestSnapshot(unittest.TestCase):
    def test_reads_the_resource(self):
        client = FakeClient(result=resource(snapshot_text(2)))
        lc.snapshot(client=client)

        self.assertEqual(client.calls, [("resources/read", {"uri": lc.SNAPSHOT_URI})])

    def test_never_calls_a_tool(self):
        """Reading a resource cannot actuate; calling a tool can."""
        client = FakeClient(result=resource(snapshot_text(2)))
        lc.snapshot(client=client)

        self.assertTrue(all(method == "resources/read" for method, _ in client.calls))

    def test_returns_text_and_counts(self):
        text, shown, total = lc.snapshot(client=FakeClient(result=resource(snapshot_text(5))))

        self.assertIn("Live Context:", text)
        self.assertEqual((shown, total), (5, 5))

    def test_an_empty_snapshot_is_an_error(self):
        with self.assertRaises(MCPError):
            lc.snapshot(client=FakeClient(result=resource("   ")))

    def test_missing_contents_explains_the_likely_cause(self):
        with self.assertRaises(MCPError) as caught:
            lc.snapshot(client=FakeClient(result={"contents": []}))

        self.assertIn("GetLiveContext", str(caught.exception))

    def test_transport_errors_are_not_swallowed(self):
        """The tool layer decides how to degrade; this function must not decide
        for it by turning a failure into an empty-looking success."""
        client = FakeClient(error=MCPError("boom"))

        with self.assertRaises(MCPError):
            lc.snapshot(client=client)


class TestEndpoint(unittest.TestCase):
    def test_builds_the_api_mcp_path(self):
        self.assertEqual(lc.endpoint("https://ha.example.com"), "https://ha.example.com/api/mcp")

    def test_tolerates_a_trailing_slash(self):
        self.assertEqual(lc.endpoint("https://ha.example.com/"), "https://ha.example.com/api/mcp")


if __name__ == "__main__":
    unittest.main()
