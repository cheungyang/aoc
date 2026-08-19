import os
import json
import importlib.util
from typing import Dict, Any, List, Optional
from core.runners.hot_reloader import HotReloader

class GraphsLoader:
    _instance = None
    _graphs = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(GraphsLoader, cls).__new__(cls)
            cls._instance._graphs = {}
            cls._instance._watched_files = set()
            cls._instance.load_graphs()
            HotReloader().start()
        return cls._instance

    def _on_graph_changed(self, file_path: str):
        print(f"GraphsLoader: graph configuration updated at {file_path}, reloading.")
        self.load_graphs()
        from core.loaders.tools_loader import ToolsLoader
        ToolsLoader().clear_permissions_cache()

    def load_graphs(self):
        graphs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "graphs"))
        if not os.path.exists(graphs_dir):
            os.makedirs(graphs_dir, exist_ok=True)
            return

        current_ids = set()

        for item in os.listdir(graphs_dir):
            item_path = os.path.join(graphs_dir, item)
            if os.path.isdir(item_path) and not item.startswith("__") and not item.startswith("."):
                graph_py_path = os.path.join(item_path, "graph.py")
                graph_json_path = os.path.join(item_path, "graph.json")
                
                if os.path.exists(graph_py_path) and os.path.exists(graph_json_path):
                    try:
                        py_mtime = os.path.getmtime(graph_py_path)
                        json_mtime = os.path.getmtime(graph_json_path)
                        
                        # Parse JSON config
                        with open(graph_json_path, "r") as f:
                            config = json.load(f)
                            
                        graph_id = config.get("graph_id") or config.get("id") or item
                        config["graph_id"] = graph_id
                        current_ids.add(graph_id)
                        
                        # Register hot reload watching
                        if graph_json_path not in self._watched_files:
                            HotReloader().watch(graph_json_path, self._on_graph_changed)
                            self._watched_files.add(graph_json_path)
                        if graph_py_path not in self._watched_files:
                            HotReloader().watch(graph_py_path, self._on_graph_changed)
                            self._watched_files.add(graph_py_path)
                        
                        # Check cache
                        cached = self._graphs.get(graph_id)
                        if (cached is None or 
                            cached.get("py_mtime") != py_mtime or 
                            cached.get("json_mtime") != json_mtime):
                            
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
                                self._graphs[graph_id] = {
                                    "module": module,
                                    "create_graph": create_graph_fn,
                                    "prepare_input": prepare_input_fn,
                                    "format_output": format_output_fn,
                                    "graph": graph_obj,
                                    "config": config,
                                    "metadata": config,
                                    "py_mtime": py_mtime,
                                    "json_mtime": json_mtime,
                                    "folder": item
                                }
                                print(f"GraphsLoader: Loaded/Reloaded graph '{graph_id}'")
                    except Exception as e:
                        print(f"GraphsLoader: Failed to load graph in {item}: {e}")

        # Clean up removed graphs
        for gid in list(self._graphs.keys()):
            if gid not in current_ids:
                del self._graphs[gid]
                print(f"GraphsLoader: Removed graph '{gid}'")

    def list_graph_names(self) -> List[str]:
        self.load_graphs()
        return list(self._graphs.keys())

    def get_graph(self, name: str) -> Optional[Dict[str, Any]]:
        self.load_graphs()
        if name in self._graphs:
            return self._graphs[name]
        # Fallback search by metadata name
        for gid, info in self._graphs.items():
            meta = info.get("metadata", {})
            if meta.get("name") == name or meta.get("graph_id") == name:
                return info
        return None

    def get_graph_config(self, graph_id: str) -> Dict[str, Any]:
        info = self.get_graph(graph_id)
        if not info:
            return {}
        return info.get("config", {})

    def get_graph_tools(self, graph_id: str) -> Dict[str, Any]:
        config = self.get_graph_config(graph_id)
        return config.get("tools", {})

    def get_graph_skills(self, graph_id: str) -> List[str]:
        config = self.get_graph_config(graph_id)
        return config.get("skills", [])

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
            display_name = metadata.get("name", name)
            overview += f"- {display_name} (id:{name}): {desc}\n"
            
        overview += "</subgraphs_list>"
        return overview

    # Backward compatibility aliases
    load_subgraphs = load_graphs
    list_subgraph_names = list_graph_names
    get_subgraph = get_graph
    get_subgraphs_overview = get_graphs_overview


SubgraphsLoader = GraphsLoader
