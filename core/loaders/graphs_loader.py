import os
import importlib.util
from typing import Dict, Any, List

class GraphsLoader:
    _instance = None
    _graphs = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(GraphsLoader, cls).__new__(cls)
            cls._instance._graphs = {}
            cls._instance.load_graphs()
        return cls._instance

    def _parse_frontmatter(self, filepath: str) -> Dict[str, str]:
        metadata = {}
        if not os.path.exists(filepath):
            return metadata
            
        try:
            with open(filepath, "r") as f:
                content = f.read()
                
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    yaml_text = parts[1]
                    for line in yaml_text.strip().split("\n"):
                        if ":" in line:
                            k, v = line.split(":", 1)
                            metadata[k.strip()] = v.strip()
        except Exception as e:
            print(f"GraphsLoader: Error parsing frontmatter from {filepath}: {e}")
        return metadata

    def load_graphs(self):
        graphs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "graphs"))
        if not os.path.exists(graphs_dir):
            os.makedirs(graphs_dir)
            return

        current_names = set()

        for item in os.listdir(graphs_dir):
            item_path = os.path.join(graphs_dir, item)
            if os.path.isdir(item_path) and not item.startswith("__") and not item.startswith("."):
                graph_py_path = os.path.join(item_path, "graph.py")
                graph_md_path = os.path.join(item_path, "GRAPH.md")
                
                if os.path.exists(graph_py_path) and os.path.exists(graph_md_path):
                    try:
                        py_mtime = os.path.getmtime(graph_py_path)
                        md_mtime = os.path.getmtime(graph_md_path)
                        
                        # Parse metadata first
                        metadata = self._parse_frontmatter(graph_md_path)
                        graph_name = metadata.get("name", item)
                        current_names.add(graph_name)
                        
                        # Check cache
                        cached = self._graphs.get(graph_name)
                        if (cached is None or 
                            cached.get("py_mtime") != py_mtime or 
                            cached.get("md_mtime") != md_mtime):
                            
                            # Load compiled graph from graph.py
                            spec = importlib.util.spec_from_file_location(f"graphs.{item}.graph", graph_py_path)
                            if spec is None or spec.loader is None:
                                continue
                            import sys
                            module = importlib.util.module_from_spec(spec)
                            sys.modules[f"graphs.{item}.graph"] = module
                            spec.loader.exec_module(module)
                            
                            create_graph_fn = getattr(module, "create_graph", None)
                            prepare_input_fn = getattr(module, "prepare_input", None)
                            format_output_fn = getattr(module, "format_output", None)
                            graph_obj = getattr(module, "graph", None)
                            
                            if create_graph_fn is not None or graph_obj is not None:
                                self._graphs[graph_name] = {
                                    "module": module,
                                    "create_graph": create_graph_fn,
                                    "prepare_input": prepare_input_fn,
                                    "format_output": format_output_fn,
                                    "graph": graph_obj,
                                    "metadata": metadata,
                                    "py_mtime": py_mtime,
                                    "md_mtime": md_mtime,
                                    "folder": item
                                }
                                print(f"GraphsLoader: Loaded/Reloaded graph '{graph_name}'")
                    except Exception as e:
                        print(f"GraphsLoader: Failed to load graph in {item}: {e}")

        # Clean up removed graphs
        for name in list(self._graphs.keys()):
            if name not in current_names:
                del self._graphs[name]
                print(f"GraphsLoader: Removed graph '{name}'")

    def list_graph_names(self) -> List[str]:
        self.load_graphs()
        return list(self._graphs.keys())

    def get_graph(self, name: str) -> Dict[str, Any] | None:
        self.load_graphs()
        return self._graphs.get(name)

    def get_graphs_overview(self, agent_id: str = None) -> str:
        if agent_id:
            from core.loaders.tools_loader import ToolsLoader
            if not ToolsLoader().check_permission(agent_id, "graph_call"):
                return ""
        self.load_graphs()
        overview = "<subgraphs_list>\n"
        overview += "The following lists the names and descriptions of the subgraphs that you have access to. "
        overview += "To execute a graph, use the `graph_call` tool with the graph name and your query.\n"
        
        for name, info in self._graphs.items():
            if name == "main":
                continue
            metadata = info.get("metadata", {})
            desc = metadata.get("description", "No description available.")
            overview += f"- {name}: {desc}\n"
            
        overview += "</subgraphs_list>"
        return overview

    # Backward compatibility aliases
    load_subgraphs = load_graphs
    list_subgraph_names = list_graph_names
    get_subgraph = get_graph
    get_subgraphs_overview = get_graphs_overview


SubgraphsLoader = GraphsLoader
