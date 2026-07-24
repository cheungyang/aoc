import unittest
import os
import sys
import asyncio

# Add root directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.loaders.subgraphs_loader import SubgraphsLoader
from tools.build_subgraph import build_subgraph

class TestSubgraphInjection(unittest.IsolatedAsyncioTestCase):
    def test_subgraph_loading(self):
        loader = SubgraphsLoader()
        # Verify loader discovers test_subgraph
        names = loader.list_subgraph_names()
        self.assertIn("test_subgraph", names)
        
        # Verify metadata is loaded correctly
        info = loader.get_subgraph("test_subgraph")
        self.assertIsNotNone(info)
        self.assertEqual(info["metadata"]["name"], "test_subgraph")
        self.assertIn("dummy test subgraph", info["metadata"]["description"])

    def test_get_subgraphs_overview(self):
        loader = SubgraphsLoader()
        overview = loader.get_subgraphs_overview()
        self.assertIn("<subgraphs_list>", overview)
        self.assertIn("test_subgraph", overview)
        self.assertIn("dummy test subgraph", overview)
        self.assertIn("</subgraphs_list>", overview)

    async def test_build_subgraph_tool_execution(self):
        # Trigger the subgraph using the build_subgraph tool
        # Wait, since build_subgraph is a LangChain tool decorated with @tool,
        # we can invoke it via .ainvoke or by calling its coroutine function directly.
        # Calling .ainvoke is cleaner as it simulates LangChain's execution path:
        response = await build_subgraph.ainvoke({
            "subgraph_name": "test_subgraph",
            "query": "hello"
        })
        
        # The expected output is formatted using format_tool_response
        self.assertIn("Hello from the test subgraph!", response)
        self.assertIn("<build_subgraph_response>", response)
        self.assertIn("<errors>None</errors>", response)

    def test_tools_loader_includes_build_subgraph(self):
        from core.loaders.tools_loader import ToolsLoader
        tools = ToolsLoader().get_tools("main")
        tool_names = [t.name for t in tools]
        self.assertIn("build_subgraph", tool_names)

    def test_subgraphs_hot_reloading(self):
        import shutil
        import time
        loader = SubgraphsLoader()
        
        # Define paths for a temporary test subgraph
        subgraphs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "subgraphs"))
        temp_dir = os.path.join(subgraphs_dir, "temp_test_subgraph")
        os.makedirs(temp_dir, exist_ok=True)
        
        temp_md_path = os.path.join(temp_dir, "GRAPH.md")
        temp_py_path = os.path.join(temp_dir, "graph.py")
        
        try:
            # Write initial files
            with open(temp_md_path, "w") as f:
                f.write("---\nname: temp_test_subgraph\ndescription: Temp subgraph description.\n---\n")
            with open(temp_py_path, "w") as f:
                f.write("from langgraph.graph import StateGraph, START, END\nworkflow = StateGraph(dict)\nworkflow.add_node('dummy', lambda x: x)\nworkflow.add_edge(START, 'dummy')\nworkflow.add_edge('dummy', END)\ngraph = workflow.compile()\n")
                
            # Verify it is loaded
            names = loader.list_subgraph_names()
            self.assertIn("temp_test_subgraph", names)
            info = loader.get_subgraph("temp_test_subgraph")
            self.assertEqual(info["metadata"]["description"], "Temp subgraph description.")
            
            # Change the metadata (simulate update)
            time.sleep(1.1)  # Sleep longer to guarantee mtime resolution change on all filesystems
            with open(temp_md_path, "w") as f:
                f.write("---\nname: temp_test_subgraph\ndescription: Updated description.\n---\n")
                
            # Verify it is reloaded
            info = loader.get_subgraph("temp_test_subgraph")
            self.assertEqual(info["metadata"]["description"], "Updated description.")
            
        finally:
            # Clean up the directory
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                
        # Verify it is removed
        names = loader.list_subgraph_names()
        self.assertNotIn("temp_test_subgraph", names)

if __name__ == "__main__":
    unittest.main()

