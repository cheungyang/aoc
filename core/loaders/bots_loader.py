from core.loaders.agents_loader import AgentsLoader
from core.runners.bot_runner import BotRunner
from core.util.config import Config

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
            # print(f"BotsLoader: No discord_token_key defined for agent {agent_id}.")
            return None
            
        token = Config().get(token_key)
        if not token or token == "your_discord_bot_token_here":
            print(f"BotsLoader: Token key {token_key} not found or invalid in env for agent {agent_id}.")
            return None
            
        print(f"BotsLoader: Creating bot for agent {agent_id} with token from {token_key}")
        bot = BotRunner(token, agent_id)
        self._bots[agent_id] = bot
        return bot

    @staticmethod
    def _match_channel(channel_obj, target_name: str) -> bool:
        if not target_name or not channel_obj:
            return False
        clean_target = str(target_name).lstrip("#").lower()
        ch_name = getattr(channel_obj, "name", "").lower()
        ch_id = str(getattr(channel_obj, "id", ""))
        return ch_name == clean_target or ch_id == clean_target or ch_name == str(target_name).lower()

    @staticmethod
    def _get_guild_channels(guild) -> list:
        channels = list(getattr(guild, "text_channels", []))
        if hasattr(guild, "voice_channels"):
            for vc in guild.voice_channels:
                if vc not in channels:
                    channels.append(vc)
        if hasattr(guild, "threads"):
            for th in guild.threads:
                if th not in channels:
                    channels.append(th)
        return channels

    def get_channel(self, agent_id, channel_name: str = None):
        bot_runner = self.get_bot(agent_id)
        if bot_runner and bot_runner.bot:
            loader = AgentsLoader()
            agent = loader.get_agent(agent_id)
            channel_hosts = agent.get_config("channel_hosts", []) if agent else []
            clean_target = str(channel_name).lstrip("#") if channel_name else None
            
            for guild in bot_runner.bot.guilds:
                for ch in self._get_guild_channels(guild):
                    if channel_name:
                        if self._match_channel(ch, channel_name) and (
                            ch.name in channel_hosts or str(ch.id) in channel_hosts or (clean_target and clean_target in channel_hosts)
                        ):
                            return ch
                    else:
                        if ch.name in channel_hosts or str(ch.id) in channel_hosts:
                            return ch
        return None

    def find_channel(self, channel_name: str):
        if not channel_name:
            return None
        loader = AgentsLoader()
        clean_target = str(channel_name).lstrip("#")
        
        # 1. Primary: Prefer the bot whose agent lists this channel in channel_hosts
        for agent_id, bot_runner in self._bots.items():
            if bot_runner and bot_runner.bot:
                agent = loader.get_agent(agent_id)
                channel_hosts = agent.get_config("channel_hosts", []) if agent else []
                if (channel_name in channel_hosts or clean_target in channel_hosts or
                    any(str(h) == str(channel_name) or str(h) == clean_target for h in channel_hosts)):
                    for guild in bot_runner.bot.guilds:
                        for ch in self._get_guild_channels(guild):
                            if self._match_channel(ch, channel_name):
                                return ch

        # 2. Secondary: If main / concierge bot is active, use it as default host
        if "main" in self._bots and self._bots["main"] and self._bots["main"].bot:
            for guild in self._bots["main"].bot.guilds:
                for ch in self._get_guild_channels(guild):
                    if self._match_channel(ch, channel_name):
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

