import unittest
import os
import sys
import asyncio
import time
import shutil

# Add root directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.loaders.graphs_loader import GraphsLoader
from tools.graph_call import graph_call

class TestGraphsLoader(unittest.TestCase):
    def test_subgraph_loading(self):
        loader = GraphsLoader()
        names = loader.list_graph_names()
        self.assertIn("coding", names)
        self.assertIn("main", names)
        
        info = loader.get_graph("coding")
        self.assertIsNotNone(info)
        self.assertEqual(info["metadata"]["name"], "coding")
        self.assertIn("description", info["metadata"])
        self.assertIsNotNone(info["create_graph"])

        main_info = loader.get_graph("main")
        self.assertIsNotNone(main_info)
        self.assertEqual(main_info["metadata"]["name"], "main")
        self.assertIsNotNone(main_info["create_graph"])

    def test_get_graphs_overview(self):
        loader = GraphsLoader()
        overview = loader.get_graphs_overview()
        self.assertIn("<subgraphs_list>", overview)
        self.assertIn("coding", overview)
        self.assertIn("</subgraphs_list>", overview)

    def test_graphs_hot_reloading(self):
        loader = GraphsLoader()
        
        graphs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "graphs"))
        temp_dir = os.path.join(graphs_dir, "temp_test_subgraph")
        os.makedirs(temp_dir, exist_ok=True)
        
        temp_md_path = os.path.join(temp_dir, "GRAPH.md")
        temp_py_path = os.path.join(temp_dir, "graph.py")
        
        try:
            with open(temp_md_path, "w") as f:
                f.write("---\nname: temp_test_subgraph\ndescription: Temp subgraph description.\n---\n")
            with open(temp_py_path, "w") as f:
                f.write("from langgraph.graph import StateGraph, START, END\nworkflow = StateGraph(dict)\nworkflow.add_node('dummy', lambda x: x)\nworkflow.add_edge(START, 'dummy')\nworkflow.add_edge('dummy', END)\ngraph = workflow.compile()\n")
                
            names = loader.list_subgraph_names()
            self.assertIn("temp_test_subgraph", names)
            info = loader.get_subgraph("temp_test_subgraph")
            self.assertEqual(info["metadata"]["description"], "Temp subgraph description.")
            
            time.sleep(1.1)
            with open(temp_md_path, "w") as f:
                f.write("---\nname: temp_test_subgraph\ndescription: Updated description.\n---\n")
                
            info = loader.get_subgraph("temp_test_subgraph")
            self.assertEqual(info["metadata"]["description"], "Updated description.")
            
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                
        names = loader.list_subgraph_names()
        self.assertNotIn("temp_test_subgraph", names)
