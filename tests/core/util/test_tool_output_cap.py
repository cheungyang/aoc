"""Tests for cap_tool_output, the generic bound on one tool result."""
import unittest

from core.util.message_util import cap_tool_output


class TestCapToolOutput(unittest.TestCase):
    def test_short_output_is_untouched(self):
        self.assertEqual(cap_tool_output("hello", 100), "hello")

    def test_non_strings_pass_through(self):
        pair = ("content", {"artifact": 1})
        self.assertIs(cap_tool_output(pair, 1), pair)

    def test_zero_disables(self):
        text = "x" * 1000
        self.assertIs(cap_tool_output(text, 0), text)

    def test_long_output_keeps_head_and_tail(self):
        text = "<payload>" + "a" * 10000 + "</payload><errors>None</errors>"
        out = cap_tool_output(text, 1000)
        self.assertLess(len(out), 1300)
        self.assertTrue(out.startswith("<payload>"))
        self.assertTrue(out.endswith("</payload><errors>None</errors>"))
        self.assertIn("tool output truncated", out)

    def test_inline_image_is_not_counted(self):
        b64 = "QUJD" * 50000  # 200k chars
        text = f'<payload><instruction_result action="read_image" path="/a.jpg">{b64}</instruction_result></payload>'
        self.assertIs(cap_tool_output(text, 1000), text)

    def test_data_uri_is_not_counted(self):
        text = "see data:image/jpeg;base64," + "QUJD" * 50000
        self.assertIs(cap_tool_output(text, 1000), text)

    def test_text_beside_an_image_is_still_capped(self):
        text = "data:image/png;base64," + "QUJD" * 1000 + " " + "log line\n" * 5000
        out = cap_tool_output(text, 1000)
        self.assertIn("tool output truncated", out)


if __name__ == "__main__":
    unittest.main()
