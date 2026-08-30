import os
import inspect
import asyncio
import discord
import base64
from collections import OrderedDict
from discord.ext import commands
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools
from core.loaders.agents_loader import AgentsLoader
from core.agent.session_manager import SessionManager
from core.agent.reaction_handler import ReactionCallbackHandler
from core.util.config import Config
from core.util import format_error_message
from core.voice.voice_manager import VoiceManager

class BotRunner:
    def __init__(self, discord_token, agent_id):
        intents = discord.Intents.default()
        intents.message_content = True # Required to read message content
        intents.voice_states = True # Required for voice channel tracking
        self.bot = commands.Bot(command_prefix="!", intents=intents)
        self.discord_token = discord_token
        self.agent_id = agent_id
        self.voice_manager = VoiceManager(self)
        self._processed_message_ids = OrderedDict()
        
        # Register events
        self.bot.event(self.on_ready)
        self.bot.event(self.on_message)
        self.bot.event(self.on_voice_state_update)
        
        # Register voice commands
        @self.bot.command(name="join")
        async def cmd_join(ctx, *, channel_name: str = None):
            target = channel_name
            if not target and ctx.author.voice:
                target = str(ctx.author.voice.channel.id)
            success = await self.voice_manager.join_voice_channel(target, text_channel=ctx.channel)
            if success:
                ch_name = getattr(ctx.channel, "name", str(ctx.channel.id))
                await ctx.send(f"Connected to voice channel: `{self.voice_manager.voice_client.channel.name}` (linked to #{ch_name}) 🎙️")
            else:
                await ctx.send("Could not join voice channel.")

        @self.bot.command(name="leave")
        async def cmd_leave(ctx):
            await self.voice_manager.leave_voice_channel()
            await ctx.send("Disconnected from voice channel. 👋")

    def get_hosted_voice_channels(self, agent) -> list:
        """Resolves target voice channels for an agent from explicit voice_config or channel_hosts convention."""
        if not agent:
            return []
        voice_config = agent.config.get("voice_config", {})
        if "voice_channels" in voice_config:
            return voice_config["voice_channels"]
        if "voice_channel" in voice_config:
            return [voice_config["voice_channel"]]
            
        channel_hosts = agent.config.get("channel_hosts", [])
        return [f"{ch}-voice" for ch in channel_hosts]

    async def on_voice_state_update(self, member, before, after):
        """Auto-follows human users when they join any voice channel matching the agent's channel_hosts."""
        if member.bot:
            return
        loader = AgentsLoader()
        agent = loader.get_agent(self.agent_id)
        if not agent:
            return
        voice_config = agent.config.get("voice_config", {})
        if not voice_config.get("enabled"):
            return
            
        vc_targets = self.get_hosted_voice_channels(agent)
        
        if after.channel and any(
            after.channel.name == vc or self.voice_manager.normalize_channel_name(after.channel.name) == self.voice_manager.normalize_channel_name(vc)
            for vc in vc_targets
        ):
            if not self.voice_manager.voice_client or self.voice_manager.voice_client.channel != after.channel:
                print(f"[VoiceManager:{self.agent_id}] 🏃 User {member.display_name} joined '{after.channel.name}'. Moving bot to follow user...")
                await self.voice_manager.join_voice_channel(after.channel.name)

    async def on_ready(self):
        print(f'Logged in as Discord bot: {self.bot.user} for agent {self.agent_id}')
        await self.bot.change_presence(status=discord.Status.online, activity=discord.Game(name="with LangGraph"))
        
        # Auto-join voice channel if configured
        loader = AgentsLoader()
        agent = loader.get_agent(self.agent_id)
        if agent:
            voice_config = agent.config.get("voice_config", {})
            if voice_config.get("enabled") and voice_config.get("auto_join"):
                vc_targets = self.get_hosted_voice_channels(agent)
                async def _auto_join():
                    await self.bot.wait_until_ready()
                    await asyncio.sleep(1.5) # Allow guild cache to populate
                    targets = vc_targets if vc_targets else ["general-voice"]
                    
                    selected_target = None
                    for t in targets:
                        norm_t = self.voice_manager.normalize_channel_name(str(t))
                        for guild in self.bot.guilds:
                            for vc in guild.voice_channels:
                                if str(t).isdigit() and vc.id == int(t):
                                    selected_target = t
                                    break
                                if vc.name == t or self.voice_manager.normalize_channel_name(vc.name) == norm_t:
                                    selected_target = t
                                    break
                            if selected_target:
                                break
                        if selected_target:
                            break

                    target_to_join = selected_target or targets[0]
                    print(f"Agent {self.agent_id} auto-joining voice channel '{target_to_join}'...")
                    await self.voice_manager.join_voice_channel(target_to_join)
                asyncio.create_task(_auto_join())


    async def on_message(self, message):
        # Deduplicate incoming Discord messages by message ID.
        # Discord gateway socket reconnections or rapid client double-clicks can deliver the exact same
        # message event multiple times. By caching recently processed message IDs in a bounded LRU structure,
        # we ensure each message is processed only once, preventing duplicate LLM executions and duplicate
        # responses from being posted to the channel.
        msg_id = getattr(message, "id", None)
        if msg_id:
            if msg_id in self._processed_message_ids:
                print(f"[BotRunner:{self.agent_id}] 🛑 Dropping duplicate message event (id: {msg_id}).")
                return
            self._processed_message_ids[msg_id] = True
            if len(self._processed_message_ids) > 1000:
                self._processed_message_ids.popitem(last=False)

        # Ignore messages from other bots
        if message.author.bot and message.author != self.bot.user:
            return

        # In debug mode, ignore messages outside debug_channel
        if not Config().is_channel_allowed(message.channel):
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
            
        # Check if channel or its normalized name matches channel_hosts (e.g. general-voice -> general)
        normalized_name = self.voice_manager.normalize_channel_name(channel_name)
        is_host = (channel_name in channel_hosts) or (normalized_name in channel_hosts) or (channel_id in channel_hosts)
        
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
        if attachments:
            content_parts = []
            if content:
                content_parts.append({"type": "text", "text": content})
            
            for attachment in attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    try:
                        from core.util import compress_image_bytes
                        image_data = await attachment.read()
                        compressed_data, content_type = compress_image_bytes(image_data, max_dim=1560, quality=80)
                        base64_image = base64.b64encode(compressed_data).decode('utf-8')
                        content_parts.append({
                            "type": "image_url",
                            "image_url": {"url": f"data:{content_type};base64,{base64_image}"},
                        })
                    except Exception as e:
                        print(f"Error reading/compressing attachment: {e}")
                else:
                    content_parts.append({"type": "text", "text": f"\n[Attached file: {attachment.filename}]({attachment.url})"})
            
            if content_parts:
                content_payload = content_parts

        # Get an immutable session object for this request
        session = SessionManager().get_session(self.agent_id, source="discord", channel=message.channel)

        # For newly opened threads, ensure the thread's starter message context is seeded
        if session.is_thread():
            thread_obj = session.channel_obj
            try:
                starter_msg = getattr(thread_obj, "starter_message", None)
                if starter_msg is None and hasattr(thread_obj, "fetch_message") and message.id != getattr(thread_obj, "id", None):
                    try:
                        starter_msg = await thread_obj.fetch_message(thread_obj.id)
                    except Exception:
                        starter_msg = None
                if starter_msg and starter_msg.id != message.id and getattr(starter_msg, "content", None):
                    from core.knowledge.memory.sqlite_session_store import SqliteSessionStore
                    session_store = SqliteSessionStore()                    
                    history = session_store.load_history(session.session_id, limit=1)
                    if not history:
                        starter_author = getattr(starter_msg.author, "display_name", "User")
                        starter_text = starter_msg.content
                        if isinstance(content_payload, str):
                            content_payload = f"[Thread starter message from {starter_author}: \"{starter_text}\"]\n\n{content_payload}"
                        elif isinstance(content_payload, list):
                            content_payload = [{"type": "text", "text": f"[Thread starter message from {starter_author}: \"{starter_text}\"]\n\n"}] + content_payload
            except Exception as e:
                print(f"[BotRunner:{self.agent_id}] Note: could not seed thread starter context: {e}")

        try:
            async with message.channel.typing():
                from core.agent.streaming_handler import DiscordStreamBuffer
                stream_buffer = DiscordStreamBuffer(message.channel, edit_interval=1.5)

                async for event in agent.execute_stream(
                    content_payload,
                    session=session,
                    callbacks=[reaction_handler]
                ):
                    event_type = event.get("type")
                    if event_type == "token":
                        await stream_buffer.append_token(event.get("content", ""))
                    elif event_type == "final_response":
                        await stream_buffer.finalize(
                            final_text=event.get("text", ""),
                            response=event.get("response")
                        )

        except Exception as e:
            print(f"Error in BotRunner for agent {self.agent_id}: {e}")
            if not self.bot.is_closed():
                try:
                    await message.channel.send(format_error_message(e))
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
