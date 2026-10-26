"""What fits in a session's history (core/runtime/history/budget.py): token
estimates, the safe cut, and the cap on one tool result."""
import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core.runtime.history.budget import (
    cap_tool_output,
    estimate_message_tokens,
    estimate_total_tokens,
    find_safe_boundary,
)


class TestBudget(unittest.TestCase):

    def test_estimate_message_tokens_various_types(self):
        # None message
        self.assertEqual(estimate_message_tokens(None), 0)

        # Simple string HumanMessage
        msg1 = HumanMessage(content="Hello world")
        tokens1 = estimate_message_tokens(msg1)
        self.assertGreater(tokens1, 4)

        # Complex list content (text + image)
        msg2 = HumanMessage(content=[
            {"type": "text", "text": "Describe this image"},
            {"type": "image_url", "image_url": "data:image/png;base64,12345"}
        ])
        tokens2 = estimate_message_tokens(msg2)
        self.assertGreater(tokens2, 300)

        # AIMessage with tool_calls
        msg3 = AIMessage(
            content="I will search for flights.",
            tool_calls=[{"name": "seats_aero", "args": {"origin": "SFO", "dest": "NRT"}, "id": "call_123"}]
        )
        tokens3 = estimate_message_tokens(msg3)
        self.assertGreater(tokens3, 20)

        # ToolMessage
        msg4 = ToolMessage(content="Flight 101 available", name="seats_aero", tool_call_id="call_123")
        tokens4 = estimate_message_tokens(msg4)
        self.assertGreater(tokens4, 10)

    def test_estimate_total_tokens(self):
        messages = [
            HumanMessage(content="A" * 400),
            AIMessage(content="B" * 400),
        ]
        total = estimate_total_tokens(messages)
        self.assertGreaterEqual(total, 200)

    def test_find_safe_boundary_no_split_for_small_history(self):
        messages = [
            HumanMessage(content="Hello"),
            AIMessage(content="Hi there"),
        ]
        split_idx = find_safe_boundary(messages, window_messages=15)
        self.assertEqual(split_idx, 0)

    def test_find_safe_boundary_preserves_tool_call_pairs(self):
        messages = []
        for i in range(10):
            messages.append(HumanMessage(content=f"Turn {i} request"))
            messages.append(AIMessage(content=f"Turn {i} reply"))

        ai_tool = AIMessage(
            content="Running tools",
            tool_calls=[
                {"name": "tool_a", "args": {}, "id": "call_a"},
                {"name": "tool_b", "args": {}, "id": "call_b"}
            ]
        )
        t_msg_a = ToolMessage(content="Result A", name="tool_a", tool_call_id="call_a")
        t_msg_b = ToolMessage(content="Result B", name="tool_b", tool_call_id="call_b")
        ai_after = AIMessage(content="Final reply after tools")

        messages = messages[:14] + [ai_tool, t_msg_a, t_msg_b, ai_after] + messages[14:]

        split_idx = find_safe_boundary(messages, window_messages=8)
        recent = messages[split_idx:]

        self.assertNotIsInstance(recent[0], ToolMessage)

        for idx, m in enumerate(recent):
            if isinstance(m, ToolMessage):
                tc_id = getattr(m, "tool_call_id", None)
                preceding_ai = [
                    x for x in recent[:idx]
                    if isinstance(x, AIMessage) and getattr(x, "tool_calls", None) and any(
                        (tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)) == tc_id
                        for tc in x.tool_calls
                    )
                ]
                self.assertTrue(len(preceding_ai) > 0, f"Orphaned ToolMessage found: {tc_id}")

    def test_find_safe_boundary_always_starts_on_human_message(self):
        messages = [
            HumanMessage(content="Turn 0"),
            AIMessage(content="", tool_calls=[{"name": "t0", "args": {}, "id": "c0"}]),
            ToolMessage(content="r0", tool_call_id="c0", name="t0"),
            AIMessage(content="Turn 0 reply"),
            HumanMessage(content="Turn 1"),
            AIMessage(content="", tool_calls=[{"name": "t1_a", "args": {}, "id": "c1_a"}]),
            ToolMessage(content="r1_a", tool_call_id="c1_a", name="t1_a"),
            AIMessage(content="", tool_calls=[{"name": "t1_b", "args": {}, "id": "c1_b"}]),
            ToolMessage(content="r1_b", tool_call_id="c1_b", name="t1_b"),
            AIMessage(content="", tool_calls=[{"name": "t1_c", "args": {}, "id": "c1_c"}]),
            ToolMessage(content="r1_c", tool_call_id="c1_c", name="t1_c"),
            AIMessage(content="Turn 1 reply"),
            HumanMessage(content="Turn 2"),
            AIMessage(content="Turn 2 reply"),
        ]
        split_idx = find_safe_boundary(messages, window_messages=6)
        self.assertGreater(split_idx, 0)
        self.assertIsInstance(messages[split_idx], HumanMessage)
        self.assertEqual(messages[split_idx].content, "Turn 1")


class TestCapToolOutput(unittest.TestCase):
    """cap_tool_output, the generic bound on one tool result."""

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
