import unittest
from unittest.mock import patch
from langgraph.checkpoint.memory import MemorySaver
from graphs.content_creation.subgraph_video import create_video_production_subgraph

class TestSubgraphVideo(unittest.IsolatedAsyncioTestCase):
    async def test_video_graph_structure(self):
        graph = create_video_production_subgraph()
        nodes = graph.nodes.keys()
        self.assertIn("generate_visual_plate", nodes)
        self.assertIn("remix_video", nodes)
        self.assertIn("extract_and_qc_frames", nodes)
        self.assertIn("evaluate_video_qc", nodes)
        self.assertIn("autonomous_debugger", nodes)

if __name__ == "__main__":
    unittest.main()
