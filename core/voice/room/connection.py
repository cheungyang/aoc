"""The bot's voice connection: joining and leaving rooms, and which text
channel a room talks into.

A voice room shares its conversation with a text channel, so spoken and typed
turns land in the same context (see `resolve_text_channel`).
"""
import asyncio
import re
import time
from typing import Callable, Iterable, Optional

import discord
from discord.ext import voice_recv


def normalize_channel_name(name: str) -> str:
    """Normalizes a channel name (strips 'voice-', '-voice', 'vc-', spaces, special chars)."""
    if not name or not isinstance(name, str):
        return ""
    cleaned = name.lower()
    cleaned = re.sub(r"^(voice[-_]|vc[-_])", "", cleaned)
    cleaned = re.sub(r"([-_]voice|[-_]vc)$", "", cleaned)
    cleaned = re.sub(r"[^a-z0-9]", "", cleaned)
    return cleaned


class VoiceConnection:
    """One bot's voice client, the sink listening on it, and its linked text channel.

    `make_sink` builds the AudioSink to listen with on each join, and
    `channel_hosts` returns the agent's current channel_hosts (read per call,
    so an edited agent.json applies without a restart).
    """

    def __init__(self, bot_runner, make_sink: Callable[[], voice_recv.AudioSink],
                 channel_hosts: Callable[[], Iterable[str]]):
        self._runner = bot_runner
        self.agent_id = bot_runner.agent_id
        self._make_sink = make_sink
        self._channel_hosts = channel_hosts
        self.voice_client = None
        self.sink = None
        self.linked_text_channel = None  # Explicitly linked text channel (e.g. from !join command)

    @property
    def bot(self):
        # Read through the runner, which replaces its bot when it reconnects.
        return self._runner.bot

    @property
    def channel(self):
        """The voice channel the bot is in, or None."""
        return getattr(self.voice_client, "channel", None) if self.voice_client else None

    def is_connected(self) -> bool:
        return bool(self.voice_client and self.voice_client.is_connected())

    def own_user(self):
        """The bot's own member in voice, which it must never listen to."""
        return getattr(self.voice_client, "user", None) if self.voice_client else None

    # --- text channel ---------------------------------------------------------

    def resolve_text_channel(self, voice_channel) -> discord.abc.Messageable:
        """
        Finds the corresponding text channel to share 1:1 conversation context with voice.
        1. Uses explicitly linked channel if set (e.g. from !join command).
        2. Matches voice channel name (e.g. 'day-planning-voice' -> 'day-planning')
           against the agent's channel_hosts and guild text channels.
        3. Matches any agent channel_host in text channels (e.g. 'general').
        4. Falls back to first available text channel in guild to unify context with text.
        5. Last resort fallback to voice channel itself.
        """
        if self.linked_text_channel:
            return self.linked_text_channel

        if not voice_channel or not hasattr(voice_channel, "guild") or not voice_channel.guild:
            return voice_channel

        vc_normalized = normalize_channel_name(voice_channel.name)
        channel_hosts = list(self._channel_hosts())

        # 1. Exact normalized name match (e.g. day-planning-voice -> day-planning)
        for ch in voice_channel.guild.text_channels:
            if normalize_channel_name(ch.name) == vc_normalized:
                return ch

        # 2. Match channel_hosts if related
        for ch in voice_channel.guild.text_channels:
            ch_norm = normalize_channel_name(ch.name)
            if (ch.name in channel_hosts or str(ch.id) in channel_hosts) and (ch_norm in vc_normalized or vc_normalized in ch_norm):
                return ch

        # 3. Match any agent channel_host in text channels (e.g. general)
        for ch in voice_channel.guild.text_channels:
            if ch.name in channel_hosts or str(ch.id) in channel_hosts:
                return ch

        # 4. Fallback to first available text channel in guild to unify context with text
        if voice_channel.guild.text_channels:
            return voice_channel.guild.text_channels[0]

        # 5. Last resort fallback to the voice channel itself
        return voice_channel

    def text_channel(self):
        """The text channel the current room talks into."""
        return self.resolve_text_channel(self.channel)

    # --- rooms ----------------------------------------------------------------

    def find_voice_channel(self, room_name: str, guild=None):
        """A voice channel of the current guild (or `guild`) by exact or normalized name."""
        guild = getattr(self.channel, "guild", None) or guild
        wanted = normalize_channel_name(room_name)
        for vc in getattr(guild, "voice_channels", None) or []:
            if vc.name == room_name or normalize_channel_name(vc.name) == wanted:
                return vc
        return None

    async def wait_for(self, room, timeout: float) -> bool:
        """Waits until the bot is connected in `room`; False after `timeout` seconds."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_connected() and getattr(self.channel, "id", None) == room.id:
                return True
            await asyncio.sleep(0.1)
        return False

    # --- joining and leaving --------------------------------------------------

    def _find_target(self, channel_name_or_id) -> Optional[discord.VoiceChannel]:
        for guild in self.bot.guilds:
            # 1. Match by channel ID
            if str(channel_name_or_id).isdigit():
                target = guild.get_channel(int(channel_name_or_id))
                if target and isinstance(target, discord.VoiceChannel):
                    return target
            # 2. Match by exact or normalized channel name, else the first available
            if channel_name_or_id:
                norm_target = normalize_channel_name(str(channel_name_or_id))
                for vc in guild.voice_channels:
                    if vc.name == channel_name_or_id or normalize_channel_name(vc.name) == norm_target:
                        return vc
            elif guild.voice_channels:
                return guild.voice_channels[0]
        return None

    async def join(self, channel_name_or_id: str = None, text_channel: discord.TextChannel = None) -> bool:
        """Joins a Discord voice channel and starts listening.

        `text_channel` links the voice conversation to that text channel (the
        `!join` command). Any other join -- following the user into a room, a
        voice room switch -- clears an earlier link, so the new room resolves
        its own text channel instead of talking into the old one.
        """
        self.linked_text_channel = text_channel
        target_channel = self._find_target(channel_name_or_id)
        if not target_channel:
            available_vcs = [f"'{vc.name}'" for guild in self.bot.guilds for vc in guild.voice_channels]
            available_str = f" Available voice channels: [{', '.join(available_vcs)}]" if available_vcs else " No voice channels found in connected Discord servers."
            print(f"[VoiceManager:{self.agent_id}] Could not find voice channel '{channel_name_or_id}'.{available_str}")
            return False

        # Clean up any existing connection on the bot or guild before reconnecting
        await self._disconnect(target_channel)

        channel_name = getattr(target_channel, "name", str(target_channel))
        try:
            print(f"[VoiceManager:{self.agent_id}] Connecting to voice channel '{channel_name}' ({target_channel.id})...")

            # Explicitly set self_deaf=False and self_mute=False so Discord delivers audio packets
            self.voice_client = await target_channel.connect(
                cls=voice_recv.VoiceRecvClient,
                self_deaf=False,
                self_mute=False,
                timeout=20.0
            )

            # Wait briefly for connection state to stabilize
            for _ in range(30):
                if self.is_connected():
                    break
                await asyncio.sleep(0.1)
            if not self.is_connected():
                print(f"[VoiceManager:{self.agent_id}] Voice connection to '{channel_name}' could not be established (handshake timed out).")
                await self._disconnect(target_channel)
                return False

            self.sink = self._make_sink()
            self.voice_client.listen(self.sink)

            resolved_text = self.resolve_text_channel(target_channel)
            resolved_name = resolved_text.name if hasattr(resolved_text, "name") else str(resolved_text)
            print(f"[VoiceManager:{self.agent_id}] Connected! Context linked to text channel '#{resolved_name}'. Listening for voice...")
            return True

        except asyncio.CancelledError:
            print(f"[VoiceManager:{self.agent_id}] Voice connection to '{channel_name}' was cancelled.")
            await self._disconnect(target_channel)
            return False
        except Exception as e:
            err_msg = str(e).strip() or type(e).__name__
            print(f"[VoiceManager:{self.agent_id}] Failed to connect to voice channel '{channel_name}': {type(e).__name__}: {err_msg}")
            await self._disconnect(target_channel)
            return False

    async def leave(self):
        """Disconnects from the active voice channel and clears the link."""
        self.linked_text_channel = None
        vc = self.voice_client
        await self._disconnect()
        if vc:
            print(f"[VoiceManager:{self.agent_id}] Disconnected from voice channel.")

    async def _disconnect(self, target_channel=None):
        """Stops listening and drops the voice client -- or, if there is none,
        the guild's dangling one -- tolerating failures at every step."""
        if self.sink:
            try:
                self.sink.cleanup()
            except Exception:
                pass
            self.sink = None

        vc_to_disconnect = self.voice_client
        self.voice_client = None
        if not vc_to_disconnect and target_channel and hasattr(target_channel, "guild") and target_channel.guild:
            vc_to_disconnect = target_channel.guild.voice_client

        if vc_to_disconnect:
            try:
                if hasattr(vc_to_disconnect, "disconnect"):
                    await vc_to_disconnect.disconnect(force=True)
            except Exception as e:
                print(f"[VoiceManager:{self.agent_id}] Note: error during voice disconnect: {e}")
