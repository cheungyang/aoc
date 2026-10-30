import asyncio
import contextlib
import datetime
import time
import discord
import base64
from collections import OrderedDict
from typing import List
from discord.ext import commands
from core.channel.discord.output import DiscordOutput
from core.loaders.agents_loader import AgentsLoader
from core.runtime.commands.base import ControlContext, TypedContext
from core.runtime.commands.registry import CONTROL, TYPED
from core.runtime.foundation import events
from core.runtime.foundation.sessions import SessionManager
from core.scheduler.script_runner import ProjectScripts
from core.storage.channel_inbox import Via
from core.channel.discord.reactions import ReactionCallbackHandler
from core.channel.discord.ownership import Ownership
from core.channel.discord.radio import poll_vote_added as radio_poll_vote_added
from core.channel.discord.radio import reaction_added as radio_reaction_added
from core.channel.handoff import channel_named, pointer_line, request_line
from core.channel.inbox import mark_read, record_reply
from core.util.errors import format_error_message
from core.voice.briefing.hook import VoiceBriefing
from core.voice.listening.gate_commands import REACTIONS as GATE_REACTIONS
from core.voice.room.connection import normalize_channel_name
from core.voice.voice_manager import VoiceManager

# How much of a replied-to message is quoted back to the agent. Enough to know
# which post is meant; the full text is usually already in some session.
REPLY_EXCERPT_CHARS = 1500


def format_reply_context(author: str, posted_at, content: str) -> str:
    """The block telling the agent which message the user is replying to."""
    excerpt = (content or "").strip()
    if len(excerpt) > REPLY_EXCERPT_CHARS:
        excerpt = excerpt[:REPLY_EXCERPT_CHARS].rstrip() + " [...]"
    stamp = ""
    if posted_at is not None:
        try:
            from core.util.local_time import get_local_timezone
            stamp = posted_at.astimezone(get_local_timezone()).strftime("%Y-%m-%d %H:%M %Z")
        except Exception:
            stamp = str(posted_at)
    attrs = f'author="{author}"' + (f' posted="{stamp}"' if stamp else "")
    return f"<replying_to {attrs}>\n{excerpt}\n</replying_to>"


def posted_at(message):
    """When a Discord message was posted, in epoch seconds, or None."""
    created = getattr(message, "created_at", None)
    return created.timestamp() if isinstance(created, datetime.datetime) else None


def append_reply_context(payload, reply_context: str):
    """Adds the block *after* the user's text: the router reads who a message
    is for from its first words ("Ted, …", core/runtime/foundation/addressing.py),
    so the user's own words must stay first."""
    if isinstance(payload, list):
        return list(payload) + [{"type": "text", "text": f"\n\n{reply_context}"}]
    return f"{payload}\n\n{reply_context}" if payload else reply_context


class _ControlThread:
    """The control thread, as its commands use it (core/runtime/commands/ports.py):
    the agents this process runs bots for, whether this bot answers for them
    all, and this instance's ownership."""

    def __init__(self, runner: "BotRunner", message):
        from core.channel.discord.loader import BotsLoader
        self._runner, self._message = runner, message
        self._agent_ids = sorted(BotsLoader()._bots)

    def agent_ids(self) -> List[str]:
        return self._agent_ids

    def elected(self) -> bool:
        return bool(self._agent_ids) and self._agent_ids[0] == self._runner.agent_id

    async def claim(self) -> None:
        await Ownership().claim(self._runner.bot, self._runner.agent_id)

    def describe(self) -> str:
        return Ownership().describe(self._agent_ids)

    async def react(self, emoji: str) -> None:
        await BotRunner._react(self._message, emoji)


class BotRunner:
    def __init__(self, discord_token, agent_id):
        self.discord_token = discord_token
        self.agent_id = agent_id
        self._shutdown_requested = False
        self._processed_message_ids = OrderedDict()
        self.bot = None
        self._init_bot()
        # The voice room, with the inbox's briefings plugged in as a hook, and
        # replies written into the focus's channel as a typed message's are.
        self.voice_manager = VoiceManager(self, hooks=[VoiceBriefing()], text_output=self._voice_text_output,
                                          turn_output=DiscordOutput)
        Ownership().add_listener(self._on_ownership_change)

    @staticmethod
    def _voice_text_output(channel):
        """Where a voice turn's reply is written: streamed into its text channel."""
        from core.channel.discord.stream_buffer import DiscordStreamBuffer
        return DiscordStreamBuffer(channel, edit_interval=1.5)

    def _init_bot(self):
        intents = discord.Intents.default()
        intents.message_content = True # Required to read message content
        intents.voice_states = True # Required for voice channel tracking
        # Commands, `!help` among them, are core/runtime/commands/: on_message runs
        # them, so discord.py's own command handling and help stay unused.
        self.bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
        
        # Register events
        self.bot.event(self.on_ready)
        self.bot.event(self.on_message)
        self.bot.event(self.on_voice_state_update)
        self.bot.event(self.on_raw_reaction_add)
        self.bot.event(self.on_raw_poll_vote_add)

    async def stop(self):
        """Cleanly shuts down the bot and prevents reconnect attempts."""
        self._shutdown_requested = True
        if self.bot and not self.bot.is_closed():
            await self.bot.close()

    async def close(self):
        """Alias for stop()."""
        await self.stop()

    async def on_raw_reaction_add(self, payload):
        """✅ or ❌ on a team radio line labels that route, and on a proactive post
        it is that post's outcome (the listeners in core/channel/discord/radio.py).
        On a `!gate review` post it marks that utterance as yours or not
        (core/voice/listening/gate_commands.py). ✅ on any other post, a proactive
        one too, finishes its channel's inbox row (core/voice/briefing)."""
        emoji = str(getattr(payload, "emoji", ""))
        if emoji not in GATE_REACTIONS:
            return
        member = getattr(payload, "member", None)
        if member is None or getattr(member, "bot", False):
            return  # a DM, or a bot's own reaction
        if not Ownership().is_mine(self.agent_id):
            return
        if await radio_reaction_added(str(payload.message_id), emoji):
            return
        if not self.voice_manager.hosts_voice():
            return
        if self.voice_manager.mark_review(payload.message_id, emoji):
            return
        if emoji != "✅":
            return
        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            return
        mark_read(channel, Via.REACTION,
                  message_time=discord.utils.snowflake_time(payload.message_id).timestamp())

    async def on_raw_poll_vote_add(self, payload):
        """A vote in a poll: team radio's reason poll under a ❌ (core/channel/discord/radio.py)."""
        if self.bot.user is not None and getattr(payload, "user_id", None) == self.bot.user.id:
            return
        if not Ownership().is_mine(self.agent_id):
            return
        await radio_poll_vote_added(str(payload.message_id), int(getattr(payload, "answer_id", 0) or 0))

    async def on_voice_state_update(self, member, before, after):
        """Follows you into the voice channel and greets you there
        (VoiceManager.member_moved), if this instance owns the agent."""
        if member.bot:
            return
        if not Ownership().is_mine(self.agent_id):
            return
        await self.voice_manager.member_moved(member, before, after)

    async def on_ready(self):
        print(f'Logged in as Discord bot: {self.bot.user} for agent {self.agent_id}')
        self._touch_heartbeat()
        await self.bot.change_presence(status=discord.Status.online, activity=discord.Game(name="with LangGraph"))

        # Learn who holds which agent before answering anything; see ownership.py.
        ownership = Ownership()
        await ownership.rebuild(self.bot)
        if self.agent_id in ownership.initial_agents:
            try:
                await ownership.claim(self.bot, self.agent_id)
            except Exception as e:
                print(f"[BotRunner:{self.agent_id}] ⚠️ could not claim agent: {e}")

        await self._maybe_auto_join()

    async def _maybe_auto_join(self):
        """Joins the configured voice channel if voice auto_join is on and this
        instance owns the agent (VoiceManager.auto_join)."""
        if not Ownership().is_mine(self.agent_id):
            return
        self.voice_manager.auto_join()

    def _on_ownership_change(self, agent_id):
        """Hands voice over when this agent moves between prod and dev."""
        if agent_id != self.agent_id or not self.bot or not self.bot.is_ready():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if Ownership().is_mine(self.agent_id):
            loop.create_task(self._maybe_auto_join())
        elif self.voice_manager.has_voice_client():
            loop.create_task(self.voice_manager.leave())

    async def _handle_control_message(self, message):
        """Control-thread messages: claims, releases and user commands.

        Every bot in this process sees the same message; `apply` is idempotent,
        and a user command (core/runtime/commands/control/) answers from one bot."""
        event = Ownership().apply(message)
        if event is None or getattr(message.author, "bot", False):
            return
        ctx = ControlContext(channel=message.channel, author=message.author, agent_id=self.agent_id,
                             control=_ControlThread(self, message))
        await CONTROL.answer(message.content, ctx)

    @staticmethod
    async def _react(message, emoji):
        try:
            await message.add_reaction(emoji)
        except Exception as e:
            print(f"Could not add reaction {emoji}: {e}")

    async def _reply_context(self, message):
        """A `<replying_to>` block if `message` is a Discord reply, else None.

        Without it the agent sees only the reply text and assumes it continues
        whatever the session last discussed -- wrong when the user is answering
        an older or out-of-band post such as a scheduled report."""
        ref = getattr(message, "reference", None)
        if not isinstance(ref, discord.MessageReference) or not ref.message_id:
            return None
        try:
            target = ref.cached_message or ref.resolved
            if not isinstance(target, discord.Message):
                target = await message.channel.fetch_message(ref.message_id)
        except Exception as e:
            print(f"[BotRunner:{self.agent_id}] Could not resolve replied-to message {ref.message_id}: {e}")
            return None
        content = getattr(target, "content", None)
        if not isinstance(content, str) or not content.strip():
            return None
        author = getattr(getattr(target, "author", None), "display_name", None) or "unknown"
        return format_reply_context(author, getattr(target, "created_at", None), content)

    def _hosts(self, channel) -> bool:
        """Whether this bot's agent hosts `channel` (a thread: its parent): its
        name, its normalized name (general-voice -> general) or its id is in the
        agent's channel_hosts."""
        agent = AgentsLoader().get_agent(self.agent_id)
        channel_hosts = agent.config.get("channel_hosts", []) if agent else []
        channel_id = str(channel.id)
        channel_name = channel.name if hasattr(channel, "name") else ""
        if isinstance(channel, discord.Thread) and channel.parent:
            channel_name = channel.parent.name
        normalized_name = normalize_channel_name(channel_name)
        return (channel_name in channel_hosts) or (normalized_name in channel_hosts) or (channel_id in channel_hosts)

    def _answers(self, message, is_host: bool) -> bool:
        """Whether this bot answers `message`: the host does, unless another bot
        is tagged; any other bot only when it's tagged."""
        tagged_bots = [user for user in message.mentions if user.bot]
        is_self_tagged = self.bot.user in message.mentions
        if is_host:
            return not (tagged_bots and not is_self_tagged)
        return is_self_tagged

    async def _run_command(self, message, content: str) -> None:
        """`!name args`: the typed command it names, if this bot answers it here.
        Anything else after "!" is ignored."""
        ctx = TypedContext(
            channel=message.channel,
            author=message.author,
            agent_id=self.agent_id,
            session=SessionManager().get_session(self.agent_id, source="discord", channel=message.channel,
                                                 output=DiscordOutput(message.channel)),
            answers_here=self._answers(message, self._hosts(message.channel)),
            voice=self.voice_manager,
            is_owner=self.bot.is_owner,
            scripts=ProjectScripts(),
        )
        try:
            await TYPED.answer(content, ctx)
        except Exception as e:
            print(f"[BotRunner:{self.agent_id}] {content.split()[0]} failed: {format_error_message(e)}")

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

        ownership = Ownership()
        if ownership.is_control_channel(message.channel):
            await self._handle_control_message(message)
            return

        # Ignore messages from other bots
        if message.author.bot and message.author != self.bot.user:
            return

        # Prod and dev both receive this message; only the owner answers.
        mine = ownership.is_mine(self.agent_id, message)

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

        # A typed command: `!name args` (core/runtime/commands/typed/). A bot's
        # own message never runs one.
        if content.startswith("!"):
            if mine and not getattr(message.author, "bot", False):
                await self._run_command(message, content)
            return

        loader = AgentsLoader()
        is_host = self._hosts(message.channel)

        # You posted here, typed or by voting in a poll: whatever its agent
        # left you in this channel is finished (core/voice/briefing).
        if is_host and mine:
            mark_read(message.channel, Via.TYPED)

        if not self._answers(message, is_host):
            if is_host:
                print(f"Agent {self.agent_id} (host) yielding to tagged agent(s).")
            return

        if not mine:
            holder = ownership.holder(self.agent_id) or "prod"
            print(f"[Ownership:{ownership.label()}] {self.agent_id}: not answering message {msg_id}, held by {holder}.")
            return

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
                        from core.channel.discord.images import compress_image_bytes
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

        # Get an immutable session object for this request. Its output remembers
        # this message: a live `now` route's reaction goes on it (core/proactive).
        session = SessionManager().get_session(self.agent_id, source="discord", channel=message.channel,
                                               output=DiscordOutput(message.channel, message_id=message.id))

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
                    from core.storage.sessions import SessionStore
                    session_store = SessionStore()
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

        reply_context = await self._reply_context(message)
        if reply_context:
            content_payload = append_reply_context(content_payload, reply_context)

        final_text = ""
        # Where the reply lands, and whose it is: here and this bot's agent,
        # unless the message named another agent (a hand-off, below).
        reply_channel, reply_agent = message.channel, self.agent_id
        try:
            async with contextlib.AsyncExitStack() as typing:
                # "Typing…" shows in the channel the reply is being written in.
                await typing.enter_async_context(message.channel.typing())
                from core.channel.discord.stream_buffer import DiscordStreamBuffer
                stream_buffer = DiscordStreamBuffer(message.channel, edit_interval=1.5)

                async for event in agent.execute_stream(
                    content_payload,
                    session=session,
                    callbacks=[reaction_handler]
                ):
                    event_type = event.get("type")
                    if event_type == events.TOKEN:
                        await stream_buffer.append_token(event.get("content", ""))
                    elif event_type == events.HANDOFF:
                        # The message named another agent (graphs/main): this
                        # channel keeps the pointer, and the reply streams in
                        # that agent's channel, under what was asked.
                        target = channel_named(getattr(message, "guild", None), event.get("channel"))
                        if target is not None:
                            await stream_buffer.finalize(final_text=pointer_line(event, target))
                            await self._post_request(target, message, event)
                            stream_buffer = DiscordStreamBuffer(target, edit_interval=1.5)
                            reply_channel, reply_agent = target, event.get("agent_id") or self.agent_id
                            # discord.py re-sends "typing…" every 5 s until told to stop.
                            await typing.aclose()
                            await typing.enter_async_context(target.typing())
                    elif event_type == events.REACTION:
                        # Delegation acknowledgement. This arrives from the
                        # delegation path itself rather than from a tool
                        # callback, so it fires for both the LLM's choice and
                        # the router's -- and lands before any text exists.
                        emoji = event.get("emoji")
                        if emoji:
                            try:
                                await message.add_reaction(emoji)
                            except Exception as re:
                                print(f"[BotRunner:{self.agent_id}] Could not add routing reaction: {re}")
                    elif event_type == events.FINAL_RESPONSE:
                        final_text = event.get("text", "")
                        await stream_buffer.finalize(
                            final_text=final_text,
                            response=event.get("response")
                        )

        except Exception as e:
            print(f"Error in BotRunner for agent {self.agent_id}: {e}")
            if not self.bot.is_closed():
                try:
                    await message.channel.send(format_error_message(e))
                except Exception as se:
                    print(f"Error sending failure message: {se}")
            return

        # The reply may need you later, if you've left by the time it lands or
        # it asks you something: a candidate for the channel inbox, judged only
        # once you join voice. No model call here.
        if is_host and isinstance(final_text, str) and final_text.strip():
            record_reply(reply_channel, final_text, asked_at=posted_at(message), agent_id=reply_agent)

    async def _post_request(self, target, message, event) -> None:
        """What was asked, at the top of the channel a hand-off continues in."""
        author = getattr(message.author, "display_name", None) or "You"
        try:
            await target.send(request_line(author, message.channel, event))
        except Exception as e:
            print(f"[BotRunner:{self.agent_id}] Could not post the hand-off's request in #{event.get('channel')}: {e}")

    def _touch_heartbeat(self):
        try:
            with open("/tmp/bot_heartbeat", "w") as f:
                f.write(str(time.time()))
        except Exception:
            pass

    async def _heartbeat_loop(self):
        while not self._shutdown_requested:
            if self.bot and not self.bot.is_closed():
                self._touch_heartbeat()
            await asyncio.sleep(20)

    async def run_bot(self, max_retries: int | None = None):
        print(f"Starting Discord bot for agent {self.agent_id}...")
        delay = 5
        retries = 0
        self._shutdown_requested = False
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            while not self._shutdown_requested:
                if self.bot.is_closed():
                    self._init_bot()

                try:
                    async with self.bot:
                        await self.bot.start(self.discord_token)
                except discord.errors.LoginFailure as e:
                    print(f"[BotRunner:{self.agent_id}] Fatal login error: {e}")
                    self._shutdown_requested = True
                    raise
                except asyncio.CancelledError:
                    print(f"[BotRunner:{self.agent_id}] Bot task cancelled.")
                    self._shutdown_requested = True
                    raise
                except Exception as e:
                    print(f"[BotRunner:{self.agent_id}] Discord bot disconnected with error: {e}")

                if self._shutdown_requested:
                    print(f"[BotRunner:{self.agent_id}] Discord bot closed.")
                    break

                retries += 1
                if max_retries is not None and retries >= max_retries:
                    print(f"[BotRunner:{self.agent_id}] Max reconnect retries ({max_retries}) reached.")
                    break

                print(f"[BotRunner:{self.agent_id}] Discord bot disconnected. Reconnecting in {delay} seconds...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
