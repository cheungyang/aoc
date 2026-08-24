import os
import sys
import importlib


class ToolsLoader:
    _instance = None

    def __new__(cls, tools_dir="tools"):
        if cls._instance is None:
            cls._instance = super(ToolsLoader, cls).__new__(cls)
            cls._instance.tools_dir = tools_dir
            cls._instance.tools_cache = None
            cls._instance._agent_permissions_cache = {}
        return cls._instance

    def __init__(self, tools_dir="tools"):
        pass

    def _discover_tools(self):
        """Discovers tools and returns a dict mapping tool_name to folder."""
        if hasattr(self, '_discovered_tools') and self._discovered_tools is not None:
            return self._discovered_tools
            
        discovered = {}
        workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
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

    def _merge_tool_permissions(self, agent_id: str, graph_id: str = None) -> dict:
        from core.agent.job_manager import current_graph_id
        from core.loaders.agents_loader import AgentsLoader
        from core.loaders.skills_loader import SkillsLoader
        from core.loaders.graphs_loader import GraphsLoader
        import copy

        agent = AgentsLoader().get_agent(agent_id)
        config = agent.config
        
        active_graph = graph_id or current_graph_id.get() or config.get("graph")
        cache_key = f"{agent_id}::{active_graph or ''}"
        if cache_key in self._agent_permissions_cache:
            return self._agent_permissions_cache[cache_key]

        # Start with tool list from agent.json
        merged_tools = copy.deepcopy(config.get("tools", {}))

        def merge_tool_dict(tools_to_merge: dict):
            for tool_name, tool_scope in tools_to_merge.items():
                if tool_name in merged_tools:
                    current_scope = merged_tools[tool_name]
                    if isinstance(current_scope, dict) and isinstance(tool_scope, dict):
                        for path, paths_perms in tool_scope.items():
                            if path in current_scope:
                                if isinstance(current_scope[path], list) and isinstance(paths_perms, list):
                                    current_scope[path] = list(set(current_scope[path] + paths_perms))
                                else:
                                    current_scope[path] = paths_perms
                            else:
                                current_scope[path] = copy.deepcopy(paths_perms)
                    elif isinstance(current_scope, list) and isinstance(tool_scope, list):
                        merged_tools[tool_name] = list(set(current_scope + tool_scope))
                    else:
                        merged_tools[tool_name] = copy.deepcopy(tool_scope)
                else:
                    merged_tools[tool_name] = copy.deepcopy(tool_scope)

        # Fetch allowed skills (including graph skills) and merge their tools
        skills_loader = SkillsLoader()
        if active_graph:
            allowed_skills = skills_loader.get_allowed_skills(agent_id, graph_id=active_graph)
        else:
            allowed_skills = skills_loader.get_allowed_skills(agent_id)
        
        for skill in allowed_skills:
            skill_tools = skills_loader.get_skill_tools(skill)
            merge_tool_dict(skill_tools)

        # Merge direct tools from active graph
        if active_graph:
            graphs_loader = GraphsLoader()
            graph_tools = graphs_loader.get_graph_tools(active_graph)
            merge_tool_dict(graph_tools)

        self._agent_permissions_cache[cache_key] = merged_tools
        return merged_tools

    def check_permission(self, agent_id: str, tool_id: str, action_name: str = None, path: str = None, graph_id: str = None, **kwargs) -> bool:
        import os
        merged = self._merge_tool_permissions(agent_id, graph_id=graph_id)
        if tool_id not in merged:
            return False
            
        permissions = merged[tool_id]
        if not permissions or action_name is None:
            return True
            
        if isinstance(permissions, dict):
            target_path_to_check = path
                
            if target_path_to_check is None:
                return False
                
            workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            target_abs_path = os.path.abspath(target_path_to_check)
            
            for base_path, actions in permissions.items():
                resolved_base_path = base_path.replace("<agent_id>", agent_id)
                base_abs_path = os.path.abspath(os.path.join(workspace_root, resolved_base_path))
                if target_abs_path.startswith(base_abs_path):
                    if action_name in actions:
                        return True
            return False
        elif isinstance(permissions, list):
            return len(permissions) == 0 or "*" in permissions or (action_name and action_name in permissions)
            
        return False

    def get_tools(self, agent_id: str, graph_id: str = None):
        """Loads and returns all tool functions for a specific agent."""
        from core.loaders.agents_loader import AgentsLoader
        agent = AgentsLoader().get_agent(agent_id)
        config = agent.config
        
        merged_tools = self._merge_tool_permissions(agent_id, graph_id=graph_id)
        allowed_tool_names = list(merged_tools.keys())
        
        # Auto-include load_skill if agent has skills
        from core.loaders.skills_loader import SkillsLoader
        has_skills = SkillsLoader().get_allowed_skills(agent_id, graph_id=graph_id) if graph_id else SkillsLoader().get_allowed_skills(agent_id)
        if has_skills:
            if "load_skill" not in allowed_tool_names:
                allowed_tool_names.append("load_skill")
                
        discovered = self._discover_tools()
        tools = []
        loaded_names = []
        for tool_name, folder in sorted(discovered.items()):
            if tool_name not in allowed_tool_names:
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
         
        print(f"Loaded {len(tools)} tools for {agent_id}: {loaded_names}")
        return tools    

    def clear_permissions_cache(self):
        self._agent_permissions_cache.clear()

        

