"""The bot's voice connection: which voice channel it uses, joining and
leaving it, and whether you can hear it yet.

Which text channel a turn belongs to is not decided here: that is the focus
(focus.py), which moves when you ask for another agent, not when the bot
changes rooms.

Hearing the bot takes your client longer than Discord takes to say you
joined. Your client still connects to the voice server, then joins the call's
end-to-end encryption (DAVE) group: the bot commits you to its MLS group, your
client processes the welcome, and a key transition runs. Until then what the
bot sends is encrypted for a group you're not in, or, when the bot is the one
joining, not yet encrypted at all; either way your client drops it. A greeting
started at once was heard mid-sentence, or not at all. `until_audible` waits
for the group to hold you (`audible_to`); with no E2EE session to ask, for
your client's connect on the voice gateway (op 11, which voice_recv dispatches
as `voice_member_connect`).
"""
import asyncio
import re
import time
from typing import Any, Callable, Dict, Optional

import discord
from discord.ext import voice_recv

from core.channel.voice.foundation import settings
from core.channel.voice.room import patches
from core.loaders.channel_hosts import WILDCARD


def normalize_channel_name(name: str) -> str:
    """Normalizes a channel name (strips 'voice-', '-voice', 'vc-', spaces, special chars)."""
    if not name or not isinstance(name, str):
        return ""
    cleaned = name.lower()
    cleaned = re.sub(r"^(voice[-_]|vc[-_])", "", cleaned)
    cleaned = re.sub(r"([-_]voice|[-_]vc)$", "", cleaned)
    cleaned = re.sub(r"[^a-z0-9]", "", cleaned)
    return cleaned


# --- which voice channel -------------------------------------------------------
#
# One list of the agent's voice rooms, used three ways: the bot follows you into
# any of them, and `!join` without a name and auto-join both go to the first of
# them the server has (VoiceManager.default_room).

# The room of an agent that names no voice channel and hosts no text channel.
DEFAULT_ROOM = "general-voice"


def voice_rooms(agent_config: Optional[dict]) -> list:
    """The agent's voice rooms, best first: voice_config's `voice_channel`, then
    its `voice_channels`; with neither, `<channel>-voice` for each text channel
    its `channel_hosts` names ("*" names none); with no such channel either,
    DEFAULT_ROOM."""
    config = agent_config or {}
    voice_config = config.get("voice_config") or {}
    listed = voice_config.get("voice_channels")
    named = [voice_config.get("voice_channel"), *(listed if isinstance(listed, (list, tuple)) else ())]
    rooms = list(dict.fromkeys(str(room).strip() for room in named if room is not None and str(room).strip()))
    if not rooms:
        rooms = [f"{channel}-voice" for channel in config.get("channel_hosts", []) if channel != WILDCARD]
    return rooms or [DEFAULT_ROOM]


def is_channel(name: str, target) -> bool:
    """Whether a voice channel called `name` is `target`, exactly or normalized."""
    return name == target or normalize_channel_name(name) == normalize_channel_name(target)


def pick_voice_channel(guilds, targets: list):
    """The first of `targets` that is a voice channel in `guilds` -- by id, or
    by exact or normalized name -- else the first target."""
    selected_target = None
    for t in targets:
        norm_t = normalize_channel_name(str(t))
        for guild in guilds:
            for vc in guild.voice_channels:
                if str(t).isdigit() and vc.id == int(t):
                    selected_target = t
                    break
                if vc.name == t or normalize_channel_name(vc.name) == norm_t:
                    selected_target = t
                    break
            if selected_target:
                break
        if selected_target:
            break
    return selected_target or targets[0]


# `until_audible` looks again this often.
AUDIBLE_POLL_S = 0.1


def _user_key(user_id) -> Optional[int]:
    """A Discord user id as `_connected_at` keys it, or None."""
    try:
        return int(user_id)
    except (TypeError, ValueError):
        return None


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
        # When each member's client last connected to the voice server
        # (time.monotonic(), by user id): what `until_audible` waits for with
        # no E2EE to ask. Forgotten when they disconnect, and on every join.
        self._connected_at: Dict[int, float] = {}

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

    def in_room(self, channel_name_or_id) -> bool:
        """Whether the bot is connected to `channel_name_or_id` already: by
        id, or by exact or normalized name."""
        room = self.channel
        if room is None or not channel_name_or_id or not self.is_connected():
            return False
        wanted = str(channel_name_or_id)
        if wanted.isdigit() and getattr(room, "id", None) == int(wanted):
            return True
        name = getattr(room, "name", None)
        return isinstance(name, str) and is_channel(name, wanted)

    # --- whether you can hear the bot yet ---------------------------------------

    def audible_to(self, user_id) -> Optional[bool]:
        """Whether what the bot sends now reaches `user_id`'s ears, as far as
        E2EE can tell: True once the call's DAVE group holds them and no key
        transition is pending, False before that. None when there's no E2EE
        session to ask (protocol 0, or no voice client)."""
        state = getattr(self.voice_client, "_connection", None) if self.voice_client else None
        version = getattr(state, "dave_protocol_version", 0)
        if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
            return None
        session = getattr(state, "dave_session", None)
        if session is None or getattr(session, "ready", False) is not True:
            return False
        if getattr(state, "dave_pending_transitions", None):
            return False
        try:
            members = {str(member) for member in session.get_user_ids()}
        except Exception:
            return None
        return str(user_id) in members

    async def until_audible(self, user_id, since: float, timeout: float = settings.GREET_MAX_WAIT_S) -> str:
        """Waits until `user_id` can hear the bot, for at most `timeout`
        seconds after `since` (time.monotonic() when they joined), and says
        how it knew:

            "e2ee"       the call's E2EE group holds them, no key change pending
            "connected"  no E2EE to ask: their client connected to the voice server
            "assumed"    no signal of either kind: GREET_NO_SIGNAL_WAIT_S went by
            "timeout"    `timeout` went by first
            "gone"       the bot isn't connected any more
        """
        deadline = since + timeout
        while True:
            if not self.is_connected():
                return "gone"
            audible = self.audible_to(user_id)
            if audible:
                return "e2ee"
            now = time.monotonic()
            if audible is None:
                if self._connected_at.get(_user_key(user_id), float("-inf")) >= since - 1.0:
                    return "connected"
                if now - since >= settings.GREET_NO_SIGNAL_WAIT_S:
                    return "assumed"
            if now >= deadline:
                return "timeout"
            await asyncio.sleep(AUDIBLE_POLL_S)

    def _watch_members(self) -> None:
        """Listens on the voice client for members' clients connecting to the
        voice server and leaving it (voice_recv's op 11 and op 13 events)."""
        self._connected_at.clear()
        add_listener = getattr(self.voice_client, "add_listener", None)
        if not callable(add_listener):
            return
        try:
            add_listener(self._member_connected, name="on_voice_member_connect")
            add_listener(self._member_disconnected, name="on_voice_member_disconnect")
        except Exception as e:
            print(f"[VoiceManager:{self.agent_id}] Could not watch voice members: {e}")

    async def _member_connected(self, member, *_args) -> None:
        key = _user_key(getattr(member, "id", None))
        if key is not None:
            self._connected_at[key] = time.monotonic()

    async def _member_disconnected(self, member, *_args) -> None:
        key = _user_key(getattr(member, "id", None))
        if key is not None:
            self._connected_at.pop(key, None)

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
        patches.apply()  # libopus and the voice gateway/decoder fixes, before the first connect
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
            self._watch_members()
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
