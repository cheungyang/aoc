"""The bot's voice connection: joining and leaving the voice room.

Which text channel a turn belongs to is not decided here: that is the focus
(focus.py), which moves when you ask for another agent, not when the bot
changes rooms.
"""
import asyncio
import re
from typing import Any, Callable, Optional

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
    """One bot's voice client and the sink listening on it.

    `make_sink` builds the AudioSink to listen with on each join, and
    `on_joined(room)`, if given, is told about every successful join: a new
    voice session, whose focus starts afresh.
    """

    def __init__(self, bot_runner, make_sink: Callable[[], voice_recv.AudioSink],
                 on_joined: Optional[Callable[[Any], None]] = None):
        self._runner = bot_runner
        self.agent_id = bot_runner.agent_id
        self._make_sink = make_sink
        self._on_joined = on_joined
        self.voice_client = None
        self.sink = None

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

    async def join(self, channel_name_or_id: str = None) -> bool:
        """Joins a Discord voice channel and starts listening."""
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
            self._warn_if_no_opus()
            self.voice_client.listen(self.sink)

            print(f"[VoiceManager:{self.agent_id}] Connected to '{channel_name}'. Listening for voice...")
            if self._on_joined is not None:
                self._on_joined(target_channel)
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
        """Disconnects from the active voice channel."""
        vc = self.voice_client
        await self._disconnect()
        if vc:
            print(f"[VoiceManager:{self.agent_id}] Disconnected from voice channel.")

    def _warn_if_no_opus(self) -> None:
        """Says so if libopus can't be loaded. Without it the receive thread
        dies on the first packet of speech (OpusNotLoaded, logged once by
        voice_recv) and the bot sits in the room deaf; playback fails too.

        Checked after the sink is made, because that is what imports PyAV,
        whose bundled libopus discord.py can see on macOS (not on Linux).
        The check is the one `Decoder()` makes on the first packet.
        """
        try:
            discord.opus.Decoder.get_opus_version()
        except discord.opus.OpusNotLoaded:
            print(f"[VoiceManager:{self.agent_id}] ⚠️ libopus could not be loaded: the bot will neither "
                  "hear nor speak in voice. Install it (Debian/Docker: libopus0, macOS: brew install opus).")

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
