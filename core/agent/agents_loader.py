import os
import json
import time
from .agent_builder import AgentBuilder

class AgentsLoader:
    _instance = None
    _agents = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AgentsLoader, cls).__new__(cls)
            cls._instance._agents_cache = {}
            cls._instance._load_agents()
        return cls._instance

    def _load_agents(self):
        agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agents"))
        self._agents = []
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
                        self._agents.append(config)
                    except Exception as e:
                        print(f"Error loading config for {agent_name}: {e}")

    def list_agents(self):
        return self._agents

    def get_agent_config(self, agent_id):
        for config in self._agents:
            if config.get("id") == agent_id:
                return config
        return None

    async def get_agent(self, agent_id):  
        if agent_id in self._agents_cache:
            print(f"Serving cached agent: {agent_id}")
            return self._agents_cache[agent_id]
            
        config = self.get_agent_config(agent_id)
        if not config:
            raise ValueError(f"Agent configuration not found for: {agent_id}")
            
        builder = AgentBuilder()
        agent = await builder.build_agent(agent_id, config)
        self._agents_cache[agent_id] = agent
        return agent
