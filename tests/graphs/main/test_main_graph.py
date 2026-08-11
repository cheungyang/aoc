import unittest
import os
import sys
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage, HumanMessage

# Add root directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from graphs.main.graph import create_graph, prepare_input, format_output

class TestMainGraph(unittest.TestCase):

    def test_prepare_input_without_caller(self):
        result = prepare_input("Hello, how are you?")
        self.assertIn("messages", result)
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0]["content"], "Hello, how are you?")

    def test_prepare_input_with_caller(self):
        result = prepare_input("Hello", caller="day-planner")
        self.assertIn("messages", result)
        self.assertEqual(result["messages"][0]["content"], "<caller>day-planner</caller>\nHello")

    def test_prepare_input_does_not_duplicate_caller(self):
        result = prepare_input("<caller>existing</caller>\nHello", caller="day-planner")
        self.assertEqual(result["messages"][0]["content"], "<caller>existing</caller>\nHello")

    def test_format_output_with_messages(self):
        state = {
            "messages": [
                HumanMessage(content="Hi"),
                AIMessage(content="Hello there!")
            ]
        }
        self.assertEqual(format_output(state), "Hello there!")

    def test_format_output_fallback(self):
        state = {"raw_key": "raw_val"}
        self.assertEqual(format_output(state), str(state))

    def test_create_graph_returns_compiled_graph(self):
        mock_llm = MagicMock()
        graph = create_graph(llm=mock_llm, tools=[])
        self.assertIsNotNone(graph)
        self.assertTrue(hasattr(graph, "ainvoke"))

if __name__ == "__main__":
    unittest.main()
