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

    def _merge_tool_permissions(self, agent_id: str) -> dict:
        if agent_id in self._agent_permissions_cache:
            return self._agent_permissions_cache[agent_id]

        from core.loaders.agents_loader import AgentsLoader
        from core.loaders.skills_loader import SkillsLoader
        agent = AgentsLoader().get_agent(agent_id)
        config = agent.config
        
        # Start with tool list from agent.json
        merged_tools = config.get("tools", {}).copy()
        
        # Fetch allowed skills and merge their tools
        allowed_skills = config.get("skills", [])
        skills_loader = SkillsLoader()
        
        for skill in allowed_skills:
            skill_tools = skills_loader.get_skill_tools(skill)
            for tool_name, skill_scope in skill_tools.items():
                if tool_name in merged_tools:
                    current_scope = merged_tools[tool_name]
                    if isinstance(current_scope, dict) and isinstance(skill_scope, dict):
                        for path, paths_perms in skill_scope.items():
                            if path in current_scope:
                                current_scope[path] = list(set(current_scope[path] + paths_perms))
                            else:
                                current_scope[path] = paths_perms
                    elif isinstance(current_scope, list) and isinstance(skill_scope, list):
                        merged_tools[tool_name] = list(set(current_scope + skill_scope))
                else:
                    merged_tools[tool_name] = skill_scope

        self._agent_permissions_cache[agent_id] = merged_tools
        return merged_tools

    def check_permission(self, agent_id: str, tool_id: str, action_name: str, path: str = None, **kwargs) -> bool:
        import os
        merged = self._merge_tool_permissions(agent_id)
        permissions = merged.get(tool_id, {})
        
        if not permissions:
            return False
            
        if tool_id == "obsidian":
            vault_id = kwargs.get("vault_id")
            target_path = kwargs.get("target_path")
            if not vault_id or vault_id not in permissions:
                return False
            scopes = permissions[vault_id]
            if "write" in scopes:
                return True
            elif "read" in scopes and action_name in ["read", "file_search", "vector_search"]:
                return True
            elif "agent-scoped" in scopes and target_path:
                workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
                vault_path = os.path.abspath(os.path.join(workspace_root, vault_id))
                rel_path = os.path.relpath(target_path, vault_path)
                path_segments = rel_path.split(os.sep)
                return agent_id in path_segments
            return False

        if isinstance(permissions, dict):
            if path is None:
                return False
            workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            target_path = os.path.abspath(path)
            for base_path, actions in permissions.items():
                base_abs_path = os.path.abspath(os.path.join(workspace_root, base_path))
                if target_path.startswith(base_abs_path):
                    if action_name in actions:
                        return True
            return False
        elif isinstance(permissions, list):
            return action_name in permissions
            
        return False

    def get_tools(self, agent_id: str):
        """Loads and returns all tool functions for a specific agent."""
        from core.loaders.agents_loader import AgentsLoader
        agent = AgentsLoader().get_agent(agent_id)
        config = agent.config
        
        merged_tools = self._merge_tool_permissions(agent_id)
        allowed_tool_names = list(merged_tools.keys())
        
        # Auto-include load_skill if agent has skills
        if config.get("skills"):
            if "load_skill" not in allowed_tool_names:
                allowed_tool_names.append("load_skill")
                
        discovered = self._discover_tools()
        tools = []
        loaded_names = []
        for tool_name, folder in discovered.items():
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

        

