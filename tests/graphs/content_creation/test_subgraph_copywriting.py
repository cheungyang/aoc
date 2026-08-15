import unittest
from graphs.content_creation.subgraph_copywriting import create_copywriting_subgraph

class TestSubgraphCopywriting(unittest.IsolatedAsyncioTestCase):
    async def test_copywriting_graph_structure(self):
        graph = create_copywriting_subgraph()
        nodes = graph.nodes.keys()
        self.assertIn("draft_and_save_copy", nodes)

if __name__ == "__main__":
    unittest.main()
