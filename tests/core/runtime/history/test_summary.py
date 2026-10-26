"""The summary that stands in for pruned history (core/runtime/history/summary.py),
built without a model call."""
import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core.runtime.history.summary import build_heuristic_summary


class TestHeuristicSummary(unittest.TestCase):

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


if __name__ == "__main__":
    unittest.main()
