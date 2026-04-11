import os
import asyncio
import discord
from discord.ext import commands
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools
from core.loaders.agents_loader import AgentsLoader
from core.agent.session_manager import SessionManager
from core.agent.reaction_handler import ReactionCallbackHandler

class BotRunner:
    def __init__(self, discord_token, agent_id):
        intents = discord.Intents.default()
        intents.message_content = True # Required to read message content
        self.bot = commands.Bot(command_prefix="!", intents=intents)
        self.discord_token = discord_token
        self.agent_id = agent_id
        
        # Register events
        self.bot.event(self.on_ready)
        self.bot.event(self.on_message)

    async def on_ready(self):
        print(f'Logged in as Discord bot: {self.bot.user} for agent {self.agent_id}')
        await self.bot.change_presence(status=discord.Status.online, activity=discord.Game(name="with LangGraph"))

    async def on_message(self, message):
        # Ignore messages from all bots
        if message.author.bot:
            return

        # Skip commands (if any)
        if message.content.startswith("!"):
            await self.bot.process_commands(message)
            return

        # Read channel_hosts from agent.json        
        loader = AgentsLoader()
        config = loader.get_agent(self.agent_id).config
        channel_hosts = config.get("channel_hosts", [])
        channel_id = str(message.channel.id)
        channel_name = message.channel.name if hasattr(message.channel, "name") else ""
        is_host = (channel_name in channel_hosts) or (channel_id in channel_hosts)
        
        # Check mentions
        tagged_bots = [user for user in message.mentions if user.bot]
        is_self_tagged = self.bot.user in message.mentions
        
        # Routing logic
        if is_host:
            if tagged_bots and not is_self_tagged:
                # Another agent is tagged, let them respond
                print(f"Agent {self.agent_id} (host) yielding to tagged agent(s).")
                return
            # Otherwise, respond as host
        else:
            # Not host
            if not is_self_tagged:
                # Ignore if not tagged
                return
            # Respond if tagged

        agent = loader.get_agent(self.agent_id)
        reaction_handler = ReactionCallbackHandler(message)
        try:
            async with message.channel.typing():
                await agent.execute(message.content, source="discord", channel=message.channel, callbacks=[reaction_handler])

        except Exception as e:
            print(f"Error in BotRunner for agent {self.agent_id}: {e}")
            await message.channel.send("Sorry, I encountered an error processing the request.")

    async def run_bot(self):
        print(f"Starting Discord bot for agent {self.agent_id}...")
        async with self.bot:
            await self.bot.start(self.discord_token)
