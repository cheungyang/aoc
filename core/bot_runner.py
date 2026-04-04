import os
import asyncio
import discord
from discord.ext import commands
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools

class BotRunner:
    def __init__(self, discord_token):
        intents = discord.Intents.default()
        intents.message_content = True # Required to read message content
        self.bot = commands.Bot(command_prefix="!", intents=intents)
        self.discord_token = discord_token
        
        # Register events
        self.bot.event(self.on_ready)
        self.bot.event(self.on_message)

    async def on_ready(self):
        print(f'Logged in as Discord bot: {self.bot.user}')

    async def on_message(self, message):
        from core.util import get_session_id
        
        # Ignore messages from the bot itself
        if message.author == self.bot.user:
            return

        # Skip commands (if any)
        if message.content.startswith("!"):
            await self.bot.process_commands(message)
            return

        from core.agent_builder import AgentBuilder
        builder = AgentBuilder(self.bot.mcp_session)
        agent = await builder.build_agent()
        await agent.process_message(message, self.bot)

    async def run_bot(self):
        from core.mcp_manager import MCPClientManager
        mcp_client = MCPClientManager()
        async with mcp_client.get_session() as mcp_session:
             # Save session for AgentBuilder to use
             self.bot.mcp_session = mcp_session
             
             print("Starting Discord bot...")
             async with self.bot:
                 await self.bot.start(self.discord_token)
