import os
import sys
import importlib
from mcp import StdioServerParameters

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

    def get_server_params(self):
        """Returns a dictionary mapping tool names to StdioServerParameters."""
        if self.tools_cache is not None:
            return self.tools_cache
        
        discovered = self._discover_tools()
        params_dict = {}
        workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        main_py = os.path.join(workspace_root, "main.py")
        
        default_params = StdioServerParameters(
            command=sys.executable,
            args=[main_py, "--mcp"],
        )
        
        for tool_name in discovered:
            params_dict[tool_name] = default_params
                            
        self.tools_cache = params_dict
        return params_dict

    def load_tools(self):
        """Loads and returns all tool functions."""
        discovered = self._discover_tools()
        tools = []
        for tool_name, folder in discovered.items():
            if folder:
                module_path = f"tools.{folder}.{tool_name}"
            else:
                module_path = f"tools.{tool_name}"
            try:
                mod = importlib.import_module(module_path)
                if hasattr(mod, tool_name):
                    func = getattr(mod, tool_name)
                    tools.append(func)
            except Exception as e:
                print(f"Failed to load tool {tool_name} from {module_path}: {e}", file=sys.stderr)
        return tools
