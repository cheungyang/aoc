from core.loaders.agents_loader import AgentsLoader

class AgentManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AgentManager, cls).__new__(cls)
            cls._instance.loader = AgentsLoader()
            cls._instance.online_agents = {} # agent_id -> status
        return cls._instance

    def set_agent_status(self, agent_id, status):
        self.online_agents[agent_id] = status

    def get_agent_status(self, agent_id):
        return self.online_agents.get(agent_id, "offline")

    def list_online_agents(self):
        return [k for k, v in self.online_agents.items() if v == "online"]

    async def get_agent(self, agent_id):
        return self.loader.get_agent(agent_id)

    async def is_bot_host(self, agent_id):
        agent = await self.get_agent(agent_id)
        config = agent.config
        return "discord_token_key" in config

    async def trigger_agent(self, agent_id, prompt=None, session_id=None, callbacks=None):
        agent = await self.get_agent(agent_id)
        reply_text = await agent.execute(prompt, session_id, callbacks=callbacks)
        return reply_text
