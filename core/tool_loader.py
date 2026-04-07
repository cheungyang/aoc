import os
import sys
import importlib


class ToolLoader:
    _instance = None

    def __new__(cls, tools_dir="tools"):
        if cls._instance is None:
            cls._instance = super(ToolLoader, cls).__new__(cls)
            cls._instance.tools_dir = tools_dir
            cls._instance.tools_cache = None
        return cls._instance

    def __init__(self, tools_dir="tools"):
        pass

    def _discover_tools(self):
        """Discovers tools and returns a dict mapping tool_name to folder."""
        if hasattr(self, '_discovered_tools') and self._discovered_tools is not None:
            return self._discovered_tools
            
        discovered = {}
        workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        tools_path = os.path.join(workspace_root, self.tools_dir)
        
        if os.path.isdir(tools_path):
            for item in os.listdir(tools_path):
                item_path = os.path.join(tools_path, item)
                if os.path.isdir(item_path) and not item.startswith("__"):
                    for filename in os.listdir(item_path):
                        if filename.endswith(".py") and not filename.startswith("__"):
                            tool_name = filename[:-3]
                            discovered[tool_name] = item # folder name
                elif os.path.isfile(item_path) and item.endswith(".py") and not item.startswith("__"):
                    tool_name = item[:-3]
                    discovered[tool_name] = "" # No folder
                            
        self._discovered_tools = discovered
        return discovered

    def get_tools(self, allowed_tool_names=None):
        """Loads and returns all tool functions."""
        discovered = self._discover_tools()
        tools = []
        loaded_names = []
        for tool_name, folder in discovered.items():
            # Filter tools based on allowed_tools
            if allowed_tool_names is not None and tool_name not in allowed_tool_names:
                continue

            if folder:
                module_path = f"tools.{folder}.{tool_name}"
            else:
                module_path = f"tools.{tool_name}"
            try:
                mod = importlib.import_module(module_path)
                if hasattr(mod, tool_name):
                    func = getattr(mod, tool_name)
                    tools.append(func)
                    loaded_names.append(tool_name)
            except Exception as e:
                print(f"Failed to load tool {tool_name} from {module_path}: {e}", file=sys.stderr)
        
        print(f"Loaded {len(tools)} tools: {loaded_names}")
        return tools    
        

