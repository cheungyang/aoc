import unittest
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)
from core.util.message_util import (
    split_message,
    estimate_message_tokens,
    estimate_total_tokens,
    find_safe_boundary,
)


class TestMessageUtil(unittest.TestCase):

    def test_split_message_short(self):
        text = "Hello world"
        chunks = split_message(text)
        self.assertEqual(chunks, [text])

    def test_split_message_newline(self):
        text = "Line 1\n" + "a" * 1990 + "\nLine 2"
        chunks = split_message(text)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0], "Line 1\n" + "a" * 1990)
        self.assertEqual(chunks[1], "Line 2")

    def test_split_message_hard_split(self):
        text = "a" * 3000
        chunks = split_message(text, limit=1000)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0], "a" * 1000)
        self.assertEqual(chunks[1], "a" * 1000)
        self.assertEqual(chunks[2], "a" * 1000)

    def test_split_message_empty(self):
        chunks = split_message("")
        self.assertEqual(chunks, [])

    def test_split_message_none(self):
        chunks = split_message(None)
        self.assertEqual(chunks, [])

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


if __name__ == "__main__":
    unittest.main()
