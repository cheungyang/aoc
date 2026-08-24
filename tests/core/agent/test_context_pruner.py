import unittest
import os
import sys
from unittest.mock import patch, MagicMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)
from core.agent.context_pruner import (
    ContextPruner,
    estimate_message_tokens,
    estimate_total_tokens,
    find_safe_boundary,
    build_heuristic_summary,
    SUMMARY_PREFIX,
    SUMMARY_SUFFIX,
)
from core.util.config import Config


class TestContextPruner(unittest.TestCase):
    def setUp(self):
        Config().reset()
        self.pruner = ContextPruner()
        self.worker_patcher = patch.object(
            ContextPruner,
            '_summarize_with_graph_worker',
            return_value="Mock summarized history"
        )
        self.mock_worker = self.worker_patcher.start()

    def tearDown(self):
        self.worker_patcher.stop()
        Config().reset()

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
            HumanMessage(content="A" * 400),   # ~100 tokens + overhead
            AIMessage(content="B" * 400),     # ~100 tokens + overhead
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
        # Create a 20-message sequence with tool calls in the middle
        messages = []
        for i in range(10):
            messages.append(HumanMessage(content=f"Turn {i} request"))
            messages.append(AIMessage(content=f"Turn {i} reply"))

        # Add a tool call unit at index 14
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

        # find_safe_boundary with window_messages=8
        split_idx = find_safe_boundary(messages, window_messages=8)
        recent = messages[split_idx:]

        # Verify that recent does NOT start with a ToolMessage
        self.assertNotIsInstance(recent[0], ToolMessage)

        # Verify that if any ToolMessage is in recent, its originating AIMessage is also in recent
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

    def test_extract_existing_summary(self):
        summary_text = "User wanted to book flights to Tokyo. Found 3 award seats."
        messages = [
            SystemMessage(content=f"{SUMMARY_PREFIX}\n{summary_text}\n{SUMMARY_SUFFIX}"),
            HumanMessage(content="Now look for hotels."),
            AIMessage(content="Searching hotels now.")
        ]
        extracted, clean = self.pruner.extract_existing_summary(messages)
        self.assertEqual(extracted, summary_text)
        self.assertEqual(len(clean), 2)
        self.assertIsInstance(clean[0], HumanMessage)

    def test_build_heuristic_summary(self):
        older = [
            HumanMessage(content="Please plan a trip to Kyoto."),
            AIMessage(
                content="Checking trains and hotels.",
                tool_calls=[{"name": "hotel_search", "args": {"city": "Kyoto"}, "id": "c1"}]
            ),
            ToolMessage(content="Found Hotel Granvia Kyoto.", name="hotel_search", tool_call_id="c1"),
            AIMessage(content="Hotel Granvia Kyoto is available for 35,000 points.")
        ]
        summary = build_heuristic_summary(older, previous_summary="User previously checked flights.")
        self.assertIn("User previously checked flights", summary)
        self.assertIn("Kyoto", summary)
        self.assertIn("hotel_search", summary)

    def test_summarize_messages_delegates_to_graph_worker(self):
        older = [
            HumanMessage(content="First request"),
            AIMessage(content="First response")
        ]
        res = self.pruner.summarize_messages(older, previous_summary="")
        self.assertEqual(res, "Mock summarized history")
        self.mock_worker.assert_called_once()

    def test_summarize_messages_fallback_to_heuristic_when_worker_fails(self):
        self.mock_worker.return_value = ""

        older = [
            HumanMessage(content="User requested code refactoring for module X."),
            AIMessage(content="Refactored module X successfully.")
        ]
        res = self.pruner.summarize_messages(older, previous_summary="")
        # Should fallback to heuristic summary without crashing
        self.assertIn("module X", res)

    def test_prune_messages_under_threshold_remains_untouched(self):
        messages = [
            HumanMessage(content="Short query"),
            AIMessage(content="Short response")
        ]
        result = self.pruner.prune_messages(messages, max_tokens=30000, window_messages=15)
        self.assertEqual(result, messages)

    def test_prune_messages_exceeding_token_threshold(self):
        # Create history exceeding 30k tokens (~120k characters)
        messages = []
        for i in range(30):
            messages.append(HumanMessage(content=f"Query {i}: " + ("data " * 500)))
            messages.append(AIMessage(content=f"Response {i}: " + ("analysis " * 500)))

        self.assertGreater(estimate_total_tokens(messages), 30000)

        pruned = self.pruner.prune_messages(messages, max_tokens=30000, window_messages=10)

        # First message should be the summary SystemMessage
        self.assertIsInstance(pruned[0], SystemMessage)
        self.assertIn(SUMMARY_PREFIX, pruned[0].content)

        # Pruned length should be smaller than original
        self.assertLess(len(pruned), len(messages))
        # Total tokens should be drastically reduced
        self.assertLess(estimate_total_tokens(pruned), estimate_total_tokens(messages))

    def test_prune_messages_force_flag(self):
        messages = [
            HumanMessage(content=f"Query {i}")
            for i in range(12)
        ]
        pruned = self.pruner.prune_messages(messages, window_messages=4, force=True)
        self.assertIsInstance(pruned[0], SystemMessage)
        self.assertIn(SUMMARY_PREFIX, pruned[0].content)
        self.assertEqual(len(pruned), 5)  # 1 summary + 4 recent

    def test_prune_messages_respects_disabled_config(self):
        Config().context_pruning_enabled = False
        messages = [HumanMessage(content="Large text " * 10000) for _ in range(10)]
        result = self.pruner.prune_messages(messages)
        self.assertEqual(len(result), len(messages))

    def test_summarize_messages_via_graph_worker(self):
        self.mock_worker.return_value = "Graph worker generated summary of turns."

        older = [
            HumanMessage(content="First request"),
            AIMessage(content="First response")
        ]
        res = self.pruner.summarize_messages(older, previous_summary="")
        self.assertEqual(res, "Graph worker generated summary of turns.")
        self.mock_worker.assert_called_once()

    def test_find_safe_boundary_always_starts_on_human_message(self):
        # Construct scenario where target_idx falls within consecutive AIMessage/ToolMessage sequence
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
        # Total 14 messages. If window_messages=6, target_idx = 8 (which is ToolMessage r1_b)
        split_idx = find_safe_boundary(messages, window_messages=6)
        self.assertGreater(split_idx, 0)
        self.assertIsInstance(messages[split_idx], HumanMessage)
        self.assertEqual(messages[split_idx].content, "Turn 1")

    def test_prune_messages_guarantees_human_message_at_start_of_recent(self):
        messages = [
            HumanMessage(content="Initial query"),
            AIMessage(content="", tool_calls=[{"name": "t0", "args": {}, "id": "c0"}]),
            ToolMessage(content="output 0", tool_call_id="c0", name="t0"),
            AIMessage(content="Analysis 0"),
            HumanMessage(content="Followup query"),
            AIMessage(content="", tool_calls=[{"name": "t1", "args": {}, "id": "c1"}]),
            ToolMessage(content="output 1", tool_call_id="c1", name="t1"),
            AIMessage(content="", tool_calls=[{"name": "t2", "args": {}, "id": "c2"}]),
            ToolMessage(content="output 2", tool_call_id="c2", name="t2"),
            AIMessage(content="Analysis 1"),
            HumanMessage(content="Latest query"),
        ]
        pruned = self.pruner.prune_messages(messages, window_messages=5, force=True)
        # First message is summary SystemMessage
        self.assertIsInstance(pruned[0], SystemMessage)
        # First dialogue message after SystemMessage MUST be HumanMessage
        self.assertIsInstance(pruned[1], HumanMessage)
        self.assertNotIsInstance(pruned[1], (AIMessage, ToolMessage))


if __name__ == "__main__":
    unittest.main()
