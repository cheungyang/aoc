import os
from core.loaders.agents_loader import AgentsLoader
from core.runners.bot_runner import BotRunner

class BotsLoader:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BotsLoader, cls).__new__(cls)
            cls._instance._bots = {} # agent_id -> BotRunner
        return cls._instance

    def get_bot(self, agent_id):
        if agent_id in self._bots:
            return self._bots[agent_id]

        loader = AgentsLoader()
        agent = loader.get_agent(agent_id)
        
        # Use get_config as requested
        token_key = agent.get_config("discord_token_key")
        
        if not token_key:
            print(f"BotsLoader: No discord_token_key defined for agent {agent_id}.")
            return None
            
        token = os.getenv(token_key)
        if not token or token == "your_discord_bot_token_here":
            print(f"BotsLoader: Token key {token_key} not found or invalid in env for agent {agent_id}.")
            return None
            
        print(f"BotsLoader: Creating bot for agent {agent_id} with token from {token_key}")
        bot = BotRunner(token, agent_id)
        self._bots[agent_id] = bot
        return bot
