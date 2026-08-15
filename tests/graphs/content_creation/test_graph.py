import unittest
from core.loaders.graphs_loader import GraphsLoader
from graphs.content_creation.graph import create_graph

class TestGraph(unittest.TestCase):
    def test_graphs_loader_discovery(self):
        loader = GraphsLoader()
        graph_info = loader.get_graph("content_creation")
        self.assertIsNotNone(graph_info)
        self.assertEqual(graph_info["metadata"].get("name"), "content_creation")
        self.assertIsNotNone(graph_info["create_graph"])


