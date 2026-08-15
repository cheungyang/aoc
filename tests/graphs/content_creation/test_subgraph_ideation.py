import unittest
from unittest.mock import patch
from langgraph.checkpoint.memory import MemorySaver
from graphs.content_creation.subgraph_ideation import create_ideation_subgraph

class TestSubgraphIdeation(unittest.IsolatedAsyncioTestCase):
    async def test_ideation_graph_structure(self):
        graph = create_ideation_subgraph()
        # Verify node existence
        nodes = graph.nodes.keys()
        self.assertIn("setup_and_generate_image", nodes)
        self.assertIn("draft_video_plot", nodes)
        self.assertIn("audit_video_plot", nodes)
        self.assertIn("hitl_image_and_plot_approval", nodes)
        self.assertIn("process_gate1_feedback", nodes)

if __name__ == "__main__":
    unittest.main()
