import os
import importlib.util
from typing import Dict, Any, List

class SubgraphsLoader:
    _instance = None
    _subgraphs = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SubgraphsLoader, cls).__new__(cls)
            cls._instance._subgraphs = {}
            cls._instance.load_subgraphs()
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
            print(f"SubgraphsLoader: Error parsing frontmatter from {filepath}: {e}")
        return metadata

    def load_subgraphs(self):
        subgraphs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "subgraphs"))
        if not os.path.exists(subgraphs_dir):
            os.makedirs(subgraphs_dir)
            return

        current_names = set()

        for item in os.listdir(subgraphs_dir):
            item_path = os.path.join(subgraphs_dir, item)
            if os.path.isdir(item_path) and not item.startswith("__") and not item.startswith("."):
                graph_py_path = os.path.join(item_path, "graph.py")
                graph_md_path = os.path.join(item_path, "GRAPH.md")
                
                if os.path.exists(graph_py_path) and os.path.exists(graph_md_path):
                    try:
                        py_mtime = os.path.getmtime(graph_py_path)
                        md_mtime = os.path.getmtime(graph_md_path)
                        
                        # Parse metadata first
                        metadata = self._parse_frontmatter(graph_md_path)
                        subgraph_name = metadata.get("name", item)
                        current_names.add(subgraph_name)
                        
                        # Check cache
                        cached = self._subgraphs.get(subgraph_name)
                        if (cached is None or 
                            cached.get("py_mtime") != py_mtime or 
                            cached.get("md_mtime") != md_mtime):
                            
                            # Load compiled graph from graph.py
                            spec = importlib.util.spec_from_file_location(f"subgraphs.{item}.graph", graph_py_path)
                            if spec is None or spec.loader is None:
                                continue
                            import sys
                            module = importlib.util.module_from_spec(spec)
                            sys.modules[f"subgraphs.{item}.graph"] = module
                            spec.loader.exec_module(module)
                            
                            graph_obj = getattr(module, "graph", None)
                            if graph_obj is not None:
                                self._subgraphs[subgraph_name] = {
                                    "graph": graph_obj,
                                    "metadata": metadata,
                                    "py_mtime": py_mtime,
                                    "md_mtime": md_mtime,
                                    "folder": item
                                }
                                print(f"SubgraphsLoader: Loaded/Reloaded subgraph '{subgraph_name}'")
                    except Exception as e:
                        print(f"SubgraphsLoader: Failed to load subgraph in {item}: {e}")

        # Clean up removed subgraphs
        for name in list(self._subgraphs.keys()):
            if name not in current_names:
                del self._subgraphs[name]
                print(f"SubgraphsLoader: Removed subgraph '{name}'")

    def list_subgraph_names(self) -> List[str]:
        self.load_subgraphs()
        return list(self._subgraphs.keys())

    def get_subgraph(self, name: str) -> Dict[str, Any] | None:
        self.load_subgraphs()
        return self._subgraphs.get(name)

    def get_subgraphs_overview(self) -> str:
        self.load_subgraphs()
        overview = "<subgraphs_list>\n"
        overview += "The following lists the names and descriptions of the subgraphs that you have access to. "
        overview += "To execute a subgraph, use the `build_subgraph` tool with the subgraph name and your query.\n"
        
        for name, info in self._subgraphs.items():
            metadata = info.get("metadata", {})
            desc = metadata.get("description", "No description available.")
            overview += f"- {name}: {desc}\n"
            
        overview += "</subgraphs_list>"
        return overview
