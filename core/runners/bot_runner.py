import os
import asyncio
import discord
import base64
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
        # Ignore messages from other bots
        if message.author.bot and message.author != self.bot.user:
            return

        content = message.content
        # If it is from this bot, check if it is a vote message
        if message.author == self.bot.user:
            if content.startswith("<@") and ": " in content:
                # Strip the mention and colon to pass only the response to the agent
                parts = content.split(": ", 1)
                if len(parts) > 1:
                    content = parts[1]
            else:
                # Ignore other messages from self to prevent loops
                return

        # Skip commands (if any)
        if content.startswith("!"):
            await self.bot.process_commands(message)
            return

        # Read channel_hosts from agent.json        
        loader = AgentsLoader()
        config = loader.get_agent(self.agent_id).config
        channel_hosts = config.get("channel_hosts", [])
        channel_id = str(message.channel.id)
        channel_name = message.channel.name if hasattr(message.channel, "name") else ""
        if isinstance(message.channel, discord.Thread) and message.channel.parent:
            channel_name = message.channel.parent.name
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
        
        content_payload = content
        
        attachments = list(message.attachments)
        if not attachments:
            # Look back in history to find the most recent image attachment if current has none
            async for msg in message.channel.history(limit=2):
                if msg.attachments:
                    if any(a.content_type and a.content_type.startswith("image/") for a in msg.attachments):
                        attachments = list(msg.attachments)
                        break

        if attachments:
            content_parts = []
            if content:
                content_parts.append({"type": "text", "text": content})
            
            for attachment in attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    try:
                        image_data = await attachment.read()
                        base64_image = base64.b64encode(image_data).decode('utf-8')
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{attachment.content_type};base64,{base64_image}"},
                        })
                    except Exception as e:
                        print(f"Error reading attachment: {e}")
            
            if content_parts:
                content_payload = content_parts

        try:
            async with message.channel.typing():
                await agent.execute(content_payload, source="discord", channel=message.channel, callbacks=[reaction_handler])

        except Exception as e:
            print(f"Error in BotRunner for agent {self.agent_id}: {e}")
            if not self.bot.is_closed():
                try:
                    await message.channel.send("Sorry, I encountered an error processing the request.")
                except Exception as se:
                    print(f"Error sending failure message: {se}")

    async def run_bot(self):
        print(f"Starting Discord bot for agent {self.agent_id}...")
        delay = 5
        while not self.bot.is_closed():
            try:
                async with self.bot:
                    await self.bot.start(self.discord_token)
            except Exception as e:
                print(f"Discord bot for agent {self.agent_id} stopped with error: {e}")
            
            if self.bot.is_closed():
                print(f"Discord bot for agent {self.agent_id} closed.")
                break
                
            print(f"Discord bot for agent {self.agent_id} disconnected. Reconnecting in {delay} seconds...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
