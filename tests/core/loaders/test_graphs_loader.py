import unittest
from unittest.mock import patch
import os
import sys
import json
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
        self.assertEqual(info["metadata"]["graph_id"], "coding")
        self.assertIn("description", info["metadata"])
        self.assertIn("tools", info["metadata"])
        self.assertIn("skills", info["metadata"])
        self.assertIsNotNone(info["create_graph"])

        main_info = loader.get_graph("main")
        self.assertIsNotNone(main_info)
        self.assertEqual(main_info["metadata"]["graph_id"], "main")
        self.assertIsNotNone(main_info["create_graph"])

    def test_get_graphs_overview(self):
        loader = GraphsLoader()
        overview = loader.get_graphs_overview()
        self.assertIn("<subgraphs_list>", overview)
        self.assertIn("coding", overview)
        self.assertIn("</subgraphs_list>", overview)

    @patch('core.loaders.tools_loader.ToolsLoader.check_permission')
    def test_get_graphs_overview_agent_permission(self, mock_check_permission):
        loader = GraphsLoader()
        
        # Agent with graph_call permission
        mock_check_permission.return_value = True
        overview = loader.get_graphs_overview(agent_id="main")
        self.assertIn("<subgraphs_list>", overview)
        self.assertIn("coding", overview)
        mock_check_permission.assert_called_with("main", "graph_call")

        # Agent without graph_call permission
        mock_check_permission.return_value = False
        overview_empty = loader.get_graphs_overview(agent_id="restricted_agent")
        self.assertEqual(overview_empty, "")

    def test_graphs_hot_reloading(self):
        loader = GraphsLoader()
        
        graphs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "graphs"))
        temp_dir = os.path.join(graphs_dir, "temp_test_subgraph")
        os.makedirs(temp_dir, exist_ok=True)
        
        temp_json_path = os.path.join(temp_dir, "graph.json")
        temp_py_path = os.path.join(temp_dir, "graph.py")
        
        try:
            with open(temp_json_path, "w") as f:
                json.dump({
                    "graph_id": "temp_test_subgraph",
                    "name": "temp_test_subgraph",
                    "description": "Temp subgraph description.",
                    "emoji": "🧪",
                    "tools": {"git": {}},
                    "skills": ["tdd_execution"]
                }, f)
            with open(temp_py_path, "w") as f:
                f.write("from langgraph.graph import StateGraph, START, END\nworkflow = StateGraph(dict)\nworkflow.add_node('dummy', lambda x: x)\nworkflow.add_edge(START, 'dummy')\nworkflow.add_edge('dummy', END)\ngraph = workflow.compile()\n")
                
            names = loader.list_subgraph_names()
            self.assertIn("temp_test_subgraph", names)
            info = loader.get_subgraph("temp_test_subgraph")
            self.assertEqual(info["metadata"]["description"], "Temp subgraph description.")
            self.assertEqual(loader.get_graph_skills("temp_test_subgraph"), ["tdd_execution"])
            self.assertEqual(loader.get_graph_tools("temp_test_subgraph"), {"git": {}})
            
            time.sleep(1.1)
            with open(temp_json_path, "w") as f:
                json.dump({
                    "graph_id": "temp_test_subgraph",
                    "name": "temp_test_subgraph",
                    "description": "Updated description.",
                    "emoji": "🧪",
                    "tools": {"bash": {}},
                    "skills": ["qa_evaluation"]
                }, f)
                
            info = loader.get_subgraph("temp_test_subgraph")
            self.assertEqual(info["metadata"]["description"], "Updated description.")
            self.assertEqual(loader.get_graph_skills("temp_test_subgraph"), ["qa_evaluation"])
            self.assertEqual(loader.get_graph_tools("temp_test_subgraph"), {"bash": {}})
            
        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                
        names = loader.list_subgraph_names()
        self.assertNotIn("temp_test_subgraph", names)
