import unittest
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.loaders.graphs_loader import GraphsLoader
from graphs.content_creation.graph import create_graph

class TestGraph(unittest.TestCase):
    def test_graphs_loader_discovery(self):
        loader = GraphsLoader()
        graph_info = loader.get_graph("content_creation")
        self.assertIsNotNone(graph_info)
        self.assertEqual(graph_info["metadata"].get("name"), "content_creation")
        self.assertIsNotNone(graph_info["create_graph"])

    def test_modular_3_macro_node_graph_structure(self):
        graph = create_graph()
        nodes = set(graph.nodes.keys())
        
        # Verify the 3 high-cohesion macro nodes + ask_for_audio
        self.assertIn("ingest_audio", nodes)
        self.assertIn("ask_for_audio", nodes)
        self.assertIn("ideate_package", nodes)
        self.assertIn("produce_deliverables", nodes)
        user_nodes = nodes - {"__start__", "__end__"}
        self.assertEqual(len(user_nodes), 4)

if __name__ == "__main__":
    unittest.main()
