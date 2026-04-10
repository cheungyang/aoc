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

    def get_channel(self, agent_id):
        bot_runner = self.get_bot(agent_id)
        if bot_runner and bot_runner.bot:
            loader = AgentsLoader()
            agent = loader.get_agent(agent_id)
            channel_hosts = agent.get_config("channel_hosts", [])
            
            for guild in bot_runner.bot.guilds:
                for ch in guild.text_channels:
                    if ch.name in channel_hosts or str(ch.id) in channel_hosts:
                        return ch
        return None

    async def reload_bot(self, agent_id):
        import asyncio
        if agent_id in self._bots:
            bot_runner = self._bots[agent_id]
            print(f"BotsLoader: Closing Discord bot for agent {agent_id}...")
            try:
                await bot_runner.bot.close()
            except Exception as e:
                print(f"BotsLoader: Error closing bot for agent {agent_id}: {e}")
            del self._bots[agent_id]
            
        # Re-instantiate and run
        print(f"BotsLoader: Reloading Discord bot for agent {agent_id}...")
        new_bot = self.get_bot(agent_id)
        if new_bot:
            asyncio.create_task(new_bot.run_bot())

