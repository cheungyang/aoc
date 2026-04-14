import os
import json
import time
from core.runners.hot_reloader import HotReloader
from core.agent.agent import Agent

class AgentsLoader:
    _instance = None
    _agents = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AgentsLoader, cls).__new__(cls)
            cls._instance._agents_cache = {}
            cls._instance._load_agents()
            HotReloader().start()
        return cls._instance

    def _on_agent_changed(self, file_path):
        import json
        import asyncio
        print(f"AgentsLoader: hot reloaded config from {file_path}")
        try:
            with open(file_path, "r") as f:
                config = json.load(f)
            agent_id = config.get("id") or config.get("agent_id")
            if not agent_id:
                agent_id = os.path.basename(os.path.dirname(file_path))
        except Exception as e:
            print(f"AgentsLoader: Error parsing file on reload: {e}")
            return

        # Invalidate caches
        self._agents_cache.clear()
        self._agent_configs.clear()
        self._load_agents()
        
        # Cascade invalidation
        from core.loaders.tools_loader import ToolsLoader
        ToolsLoader().clear_permissions_cache()
        
        from core.loaders.bots_loader import BotsLoader
        asyncio.create_task(BotsLoader().reload_bot(agent_id))

    def _load_agents(self):
        agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agents"))
        self._agent_configs = {}
        if not os.path.exists(agents_dir):
            return
        for agent_name in os.listdir(agents_dir):
            agent_path = os.path.join(agents_dir, agent_name)
            if os.path.isdir(agent_path):
                config_path = os.path.join(agent_path, "agent.json")
                if os.path.exists(config_path):
                    try:
                        with open(config_path, "r") as f:
                            config = json.load(f)
                        # Ensure ID is set
                        if "id" not in config and "agent_id" not in config:
                            config["id"] = agent_name
                        elif "agent_id" in config and "id" not in config:
                            config["id"] = config["agent_id"]
                        self._agent_configs[config["id"]] = config
                        HotReloader().watch(config_path, self._on_agent_changed)
                    except Exception as e:
                        print(f"Error loading config for {agent_name}: {e}")

    def _load_prompt_from_file(self, file_path, desc):
        if os.path.exists(file_path):
            file_name = os.path.basename(file_path)
            tag = file_name.replace('.md', '')
            with open(file_path, "r") as f:
                content = f.read()
            return f"<{tag}>\n{desc}\n\n{content}\n</{tag}>"
        return None

    def list_agent_ids(self):
        return list(self._agent_configs.keys())

    def get_agent(self, agent_id):  
        if agent_id in self._agents_cache:
            return self._agents_cache[agent_id]
            
        config = self._agent_configs.get(agent_id)
        if not config:
            raise ValueError(f"Agent configuration not found for: {agent_id}")
            
        agent = Agent(agent_id, config)
        self._agents_cache[agent_id] = agent
        return agent

    def get_agent_prompt(self, agent_id):
        agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agents"))
        agent_path = os.path.join(agents_dir, agent_id)
        pkm_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "pkm", "agents", agent_id))

        prompt_parts = []
        
        core_files = [
            ("AGENTS.md", "Your specialization and workflow:"),
            ("IDENTITY.md", "Short description of who you are:"),
            ("SOUL.md", "Your personality, behavior and guiding success in your tasks:"),
            ("USER.md", "Information about your human:")
        ]
        
        pkm_files = [
            ("MEMORY.md", "Long term memory on key decisions and learnings to make your tasks successful:"),
            ("CONTEXT.md", "Context about your human to improve personalization:"),
            ("FEEDBACK.md", "Feedbacks from human to adhere to, avoid repeating the same mistake:")
        ]

        for file_name, desc in core_files:
            part = self._load_prompt_from_file(os.path.join(agent_path, file_name), desc)
            if part:
                prompt_parts.append(part)

        for file_name, desc in pkm_files:
            part = self._load_prompt_from_file(os.path.join(pkm_dir, file_name), desc)
            if part:
                prompt_parts.append(part)

        return "\n\n".join(prompt_parts) if prompt_parts else ""
