import os
import re
import time
import asyncio
from typing import Dict, NamedTuple, Optional

import discord
from discord.ext import voice_recv
from .vad_sink import VADSink
from .stt_engine import STTEngine
from .tts_engine import TTSEngine
from .blurp_generator import BlurpGenerator
from .sentence_chunker import SentenceChunker
from .verbalizer import Verbalizer
from .voice_stream import VoiceStream
from .audio_queue import AudioStreamQueue
from .bridge_manager import BridgeManager
from .room_switch import build_room_aliases, match_room, room_label
from core.loaders.agents_loader import AgentsLoader
from core.runtime.session_manager import SessionManager
from core.runtime.turn_trace import start_turn_trace
from core.util.models import DEFAULT_VERBALIZER_MODEL

# Ensure FFmpeg executable is located
try:
    import imageio_ffmpeg
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_EXE = "ffmpeg"

# How long a finished turn's trace waits for its first audio to start playing
# before it is recorded anyway (audio can queue behind a bridge phrase).
FIRST_AUDIO_WAIT_S = 15.0

# How long a room switch waits for the bot to follow the user into the new room.
ROOM_FOLLOW_TIMEOUT_S = 8.0


class SpeechTiming(NamedTuple):
    """When the user stopped talking, and when the VAD declared it (time.monotonic())."""
    speech_end_at: Optional[float]
    vad_final_at: float


class _TurnSpeech:
    """What a turn's speech lane has done so far, and when its audio was first heard."""

    def __init__(self, trace):
        self.trace = trace
        self.emitted = False        # anything spoken (or a bridge played) this turn
        self.queued_audio = False   # answer audio handed to the audio queue
        self.heard = False          # the first answer audio started playing
        self._finish = None
        self._timer = None

    def on_audio_start(self):
        """Audio-queue callback: the listener starts hearing the answer now."""
        if self.heard:
            return
        self.heard = True
        self.trace.mark("audio_start")
        if self._finish is not None:
            self._run_finish()

    def finish_when_heard(self, finish, cancelled: bool):
        """Runs `finish` now, or once the queued answer audio starts playing.

        Short replies are often fully queued before playback begins, and time
        to first audio is the number the trace exists for, so recording waits
        for it -- bounded, because a barge-in can clear audio that never plays.
        """
        if cancelled or self.heard or not self.queued_audio:
            finish()
            return
        self._finish = finish
        try:
            self._timer = asyncio.get_running_loop().call_later(FIRST_AUDIO_WAIT_S, self._run_finish)
        except RuntimeError:
            self._run_finish()

    def _run_finish(self):
        finish, self._finish = self._finish, None
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if finish is not None:
            finish()


class VoiceManager:
    """Orchestrates Discord voice connections, VAD, static blurps, STT, and TTS playback."""
    
    def __init__(self, bot_runner):
        self.bot_runner = bot_runner
        self.agent_id = bot_runner.agent_id
        self.bot = bot_runner.bot
        self.voice_client = None
        self.vad_sink = None
        self.linked_text_channel = None # Explicitly linked text channel (e.g. from !join command)
        
        # Load agent voice configuration
        loader = AgentsLoader()
        agent = loader.get_agent(self.agent_id)
        self.config = agent.config.get("voice_config", {}) if agent else {}
        
        # Initialize STT, TTS, Bridge, and Audio Stream Queue
        stt_model = self.config.get("stt_model", "base.en")
        tts_voice = self.config.get("tts_voice", "en-US-JennyNeural")
        tts_speed = float(self.config.get("tts_speed", 1.0))
        
        self.stt_engine = STTEngine(model_size=stt_model)
        self.tts_engine = TTSEngine(default_voice=tts_voice, default_speed=tts_speed)
        # Rewrites structured agent output for the ear. Disabling it falls the
        # pipeline back to TextSanitizer, which is still speakable -- just
        # flatter -- so this is a safe switch to flip if latency matters more
        # than phrasing on a given agent.
        self.verbalizer = Verbalizer(
            model_name=self.config.get("verbalizer_model", DEFAULT_VERBALIZER_MODEL),
            enabled=self.config.get("verbalize", True),
        )
        self.bridge_manager = BridgeManager()
        self.audio_queue = AudioStreamQueue(self, loop=getattr(self.bot, "loop", None))
        
        self._lock = asyncio.Lock()
        self._active_turn = 0
        self._current_task = None

    def normalize_channel_name(self, name: str) -> str:
        """Normalizes a channel name (strips 'voice-', '-voice', 'vc-', spaces, special chars)."""
        if not name or not isinstance(name, str):
            return ""
        cleaned = name.lower()
        cleaned = re.sub(r"^(voice[-_]|vc[-_])", "", cleaned)
        cleaned = re.sub(r"([-_]voice|[-_]vc)$", "", cleaned)
        cleaned = re.sub(r"[^a-z0-9]", "", cleaned)
        return cleaned

    def resolve_corresponding_text_channel(self, voice_channel: discord.VoiceChannel) -> discord.abc.Messageable:
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

        vc_normalized = self.normalize_channel_name(voice_channel.name)
        loader = AgentsLoader()
        agent = loader.get_agent(self.agent_id)
        channel_hosts = agent.config.get("channel_hosts", []) if agent else []

        # 1. Exact normalized name match (e.g. day-planning-voice -> day-planning)
        for ch in voice_channel.guild.text_channels:
            if self.normalize_channel_name(ch.name) == vc_normalized:
                return ch

        # 2. Match channel_hosts if related
        for ch in voice_channel.guild.text_channels:
            ch_norm = self.normalize_channel_name(ch.name)
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

    async def _cleanup_failed_connection(self, target_channel=None):
        """Safely cleans up any dangling voice connection state and resources on failure."""
        if self.vad_sink:
            try:
                self.vad_sink.cleanup()
            except Exception:
                pass
            self.vad_sink = None

        vc_to_disconnect = self.voice_client
        self.voice_client = None

        if not vc_to_disconnect and target_channel and hasattr(target_channel, "guild") and target_channel.guild:
            vc_to_disconnect = target_channel.guild.voice_client

        if vc_to_disconnect:
            try:
                if hasattr(vc_to_disconnect, "disconnect"):
                    await vc_to_disconnect.disconnect(force=True)
            except Exception:
                pass

    async def join_voice_channel(self, channel_name_or_id: str = None, text_channel: discord.TextChannel = None) -> bool:
        """Joins a Discord voice channel and starts listening with VADSink.

        `text_channel` links the voice conversation to that text channel (the
        `!join` command). Any other join -- following the user into a room, a
        voice room switch -- clears an earlier link, so the new room resolves
        its own text channel instead of talking into the old one.
        """
        self.linked_text_channel = text_channel
            
        target_channel = None
        for guild in self.bot.guilds:
            # 1. Match by channel ID
            if str(channel_name_or_id).isdigit():
                target_channel = guild.get_channel(int(channel_name_or_id))
                if target_channel and isinstance(target_channel, discord.VoiceChannel):
                    break
            
            # 2. Match by exact or normalized channel name
            if channel_name_or_id:
                norm_target = self.normalize_channel_name(str(channel_name_or_id))
                for vc in guild.voice_channels:
                    if vc.name == channel_name_or_id or self.normalize_channel_name(vc.name) == norm_target:
                        target_channel = vc
                        break
            else:
                # Default to first available
                target_channel = guild.voice_channels[0] if guild.voice_channels else None
            
            if target_channel:
                break
                
        if not target_channel:
            available_vcs = [f"'{vc.name}'" for guild in self.bot.guilds for vc in guild.voice_channels]
            available_str = f" Available voice channels: [{', '.join(available_vcs)}]" if available_vcs else " No voice channels found in connected Discord servers."
            print(f"[VoiceManager:{self.agent_id}] Could not find voice channel '{channel_name_or_id}'.{available_str}")
            return False
            
        # Clean up any existing connection on the bot or guild before reconnecting
        await self._cleanup_failed_connection(target_channel)

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
            connected = False
            for _ in range(30):
                if self.voice_client and self.voice_client.is_connected():
                    connected = True
                    break
                await asyncio.sleep(0.1)

            if not connected or not self.voice_client or not self.voice_client.is_connected():
                print(f"[VoiceManager:{self.agent_id}] Voice connection to '{channel_name}' could not be established (handshake timed out).")
                await self._cleanup_failed_connection(target_channel)
                return False

            # Attach VAD sink to listen
            self.vad_sink = VADSink(self, loop=self.bot.loop)
            self.voice_client.listen(self.vad_sink)
            
            resolved_text = self.resolve_corresponding_text_channel(target_channel)
            resolved_name = resolved_text.name if hasattr(resolved_text, "name") else str(resolved_text)
            print(f"[VoiceManager:{self.agent_id}] Connected! Context linked to text channel '#{resolved_name}'. Listening for voice...")
            return True
            
        except asyncio.CancelledError:
            print(f"[VoiceManager:{self.agent_id}] Voice connection to '{channel_name}' was cancelled.")
            await self._cleanup_failed_connection(target_channel)
            return False
        except Exception as e:
            err_msg = str(e).strip() or type(e).__name__
            print(f"[VoiceManager:{self.agent_id}] Failed to connect to voice channel '{channel_name}': {type(e).__name__}: {err_msg}")
            await self._cleanup_failed_connection(target_channel)
            return False

    async def leave_voice_channel(self):
        """Disconnects from the active voice channel and clears link."""
        self.linked_text_channel = None
        if self.vad_sink:
            try:
                self.vad_sink.cleanup()
            except Exception:
                pass
            self.vad_sink = None

        vc = self.voice_client
        self.voice_client = None

        if vc:
            try:
                if hasattr(vc, "disconnect"):
                    await vc.disconnect(force=True)
            except Exception as e:
                print(f"[VoiceManager:{self.agent_id}] Note: error during voice disconnect: {e}")
            print(f"[VoiceManager:{self.agent_id}] Disconnected from voice channel.")

    def _stop_playback(self):
        """Stops active audio playback without stopping voice reception."""
        if hasattr(self, "audio_queue") and self.audio_queue:
            self.audio_queue.clear()
        elif self.voice_client:
            try:
                if hasattr(self.voice_client, "stop_playing"):
                    self.voice_client.stop_playing()
                elif hasattr(self.voice_client, "stop"):
                    self.voice_client.stop()
            except Exception:
                pass

    async def on_speech_started(self, user):
        """Called when user begins speaking. Handles barge-in by halting current audio and cancelling prior in-flight task."""
        if user and getattr(user, "bot", False) is True:
            return
        if self.voice_client and getattr(self.voice_client, "user", None) and getattr(user, "id", None) == getattr(self.voice_client.user, "id", None):
            return

        self._active_turn += 1
        
        # 1. Stop audio playback immediately (without stopping voice reception!)
        if (self.voice_client and self.voice_client.is_playing()) or (hasattr(self, "audio_queue") and self.audio_queue.is_playing()):
            user_name = getattr(user, "display_name", str(user))
            print(f"[VoiceManager:{self.agent_id}] 🛑 User {user_name} spoke during playback. Halting audio (Barge-in).")
            self._stop_playback()

        # 2. Cancel prior in-flight generation/synthesis task
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            self._current_task = None

    async def on_speech_finished(self, user, wav_bytes: bytes, speech_end_at: Optional[float] = None):
        """Called when user finishes speaking. Spawns processing task for this turn.

        `speech_end_at` is the time.monotonic() of the user's last voiced frame,
        which anchors the turn's latency trace.
        """
        timing = SpeechTiming(speech_end_at=speech_end_at, vad_final_at=time.monotonic())
        if user and getattr(user, "bot", False) is True:
            return
        if self.voice_client and getattr(self.voice_client, "user", None) and getattr(user, "id", None) == getattr(self.voice_client.user, "id", None):
            return

        # Cancel any previous task
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

        turn_id = self._active_turn
        self._current_task = asyncio.create_task(self._process_speech_turn(user, wav_bytes, turn_id, timing))

    async def _process_speech_turn(self, user, wav_bytes: bytes, turn_id: int, timing: Optional[SpeechTiming] = None):
        """Processes a speech turn: STT, then the agent's stream split into a text lane and a speech lane."""
        async with self._lock:
            if turn_id != self._active_turn:
                return
            trace = start_turn_trace(
                self.agent_id, turn_id, speech_end_at=timing.speech_end_at if timing else None
            )
            if timing:
                trace.mark("vad_final", at=timing.vad_final_at)
            speech = _TurnSpeech(trace)
            session_ident = None
            cancelled = False
            try:
                # 1. Instantly play static acoustic blurp
                print(f"[VoiceManager:{self.agent_id}] 🔔 Playing signature blurp...")
                self._play_blurp()
                
                # 2. Transcribe speech in background
                print(f"[VoiceManager:{self.agent_id}] ⚡ Transcribing audio with Faster-Whisper...")
                transcript = await self.stt_engine.transcribe(wav_bytes)
                trace.mark("stt_done")
                if not transcript:
                    print(f"[VoiceManager:{self.agent_id}] Transcription empty or filtered as noise. Ignoring.")
                    trace.meta["outcome"] = "no_transcript"
                    return
                    
                if turn_id != self._active_turn:
                    return

                author_name = getattr(user, "display_name", str(user)) if user else "Speaker"
                print(f"[VoiceManager:{self.agent_id}] 📝 Transcribed prompt from {author_name}: '{transcript}'")

                # 3. "Take me to day planning" moves rooms instead of starting a turn.
                if await self._maybe_switch_room(user, transcript, turn_id):
                    trace.meta["outcome"] = "room_switch"
                    return
                
                # 4. Resolve the corresponding text channel so context is 100% shared with text
                target_channel = self.resolve_corresponding_text_channel(self.voice_client.channel)
                target_name = getattr(target_channel, "name", str(target_channel))
                
                # Mirror the user's words to the shared text channel without
                # holding up the agent: only the text lane waits for it, so the
                # transcript still lands above the reply.
                mirror_task = None
                if self.config.get("post_transcript_to_chat", True) and target_channel:
                    mirror_task = asyncio.create_task(self._post_transcript(target_channel, author_name, transcript))
                
                # 5. Execute LangGraph Agent with streaming
                loader = AgentsLoader()
                agent = loader.get_agent(self.agent_id)
                if not agent:
                    return
                    
                print(f"[VoiceManager:{self.agent_id}] 🤖 Streaming LangGraph agent under #{target_name} context...")

                # Same session as the text channel (so context is shared), but
                # tagged as voice so token rows can tell spoken turns from typed
                # ones. The verbalizer bills its calls to this same context.
                session_ident = SessionManager().get_session(
                    agent_id=self.agent_id,
                    source="discord",
                    channel=target_channel,
                    surface="voice",
                )
                await self._run_agent_turn(agent, transcript, session_ident, target_channel, mirror_task, turn_id, speech)
                        
            except asyncio.CancelledError:
                cancelled = True
                trace.meta["outcome"] = "cancelled"
            except Exception as e:
                from core.util import format_error_message
                err_msg = format_error_message(e)
                trace.meta["outcome"] = "error"
                print(f"[VoiceManager:{self.agent_id}] Error in voice pipeline: {err_msg}")
            finally:
                if turn_id != self._active_turn and not cancelled:
                    trace.meta.setdefault("outcome", "superseded")
                trace.mark("turn_end")
                session_id = session_ident.session_id if session_ident else None
                speech.finish_when_heard(lambda: trace.finish(session_id=session_id), cancelled=cancelled)

    async def _post_transcript(self, channel, author_name: str, transcript: str):
        try:
            await channel.send(f"🎤 **{author_name} (Voice):** {transcript}")
        except Exception as te:
            print(f"[VoiceManager:{self.agent_id}] Error posting transcript: {te}")

    async def _run_agent_turn(self, agent, transcript, session, target_channel, mirror_task, turn_id, speech):
        """Streams the agent and fans its events out to a text lane and a speech lane.

        Speech and text diverge here, deliberately. The synthesiser gets text
        restructured for the ear; the channel gets what the agent actually
        wrote, markdown and all. They run as two concurrent lanes so neither
        waits on the other: a slow Discord edit no longer delays the next
        sentence's synthesis, and synthesis no longer delays reading the
        agent's stream. This loop only routes events and never blocks on I/O.
        """
        from core.channel.discord.stream_buffer import DiscordStreamBuffer

        trace = speech.trace
        voice_stream = VoiceStream(verbalizer=self.verbalizer, session=session)
        text_buffer = DiscordStreamBuffer(target_channel, edit_interval=1.5)
        text_lane: asyncio.Queue = asyncio.Queue()
        speech_lane: asyncio.Queue = asyncio.Queue()

        # Ensure audio queue is started
        self.audio_queue.start()
        text_task = asyncio.create_task(self._run_text_lane(text_buffer, text_lane, mirror_task))
        speech_task = asyncio.create_task(self._run_speech_lane(voice_stream, speech_lane, session, turn_id, speech))
        final_text = ""
        try:
            trace.mark("agent_start")
            # Stream agent execution events (tokens, tool bridge phrases, and final audio)
            async for event in agent.execute_stream(
                transcript,
                session=session
            ):
                if turn_id != self._active_turn:
                    print(f"[VoiceManager:{self.agent_id}] Discarding streaming event due to newer voice turn.")
                    return

                event_type = event.get("type")
                if event_type == "tool_start":
                    trace.event("tool_start", tool=event.get("tool_name", ""))
                    speech_lane.put_nowait(("bridge", event))

                elif event_type == "tool_end":
                    trace.event("tool_end", tool=event.get("tool_name", ""))

                elif event_type == "token":
                    token_delta = event.get("content", "")
                    if not token_delta:
                        continue
                    text_lane.put_nowait(("token", token_delta))
                    # The "🌼 Daisy: " attribution of a delegated reply is for
                    # the channel; read aloud it is noise before every answer.
                    if not event.get("is_header"):
                        trace.mark("first_token")
                        speech_lane.put_nowait(("token", token_delta))

                elif event_type == "final_response":
                    final_text = event.get("text", "") or ""
                    text_lane.put_nowait(("final", event))

                elif event_type == "error":
                    print(f"[VoiceManager:{self.agent_id}] Agent stream error: {event.get('content')}")

            speech_lane.put_nowait(("end", final_text))
            text_lane.put_nowait(None)
            await asyncio.gather(text_task, speech_task)
        finally:
            for task in (text_task, speech_task):
                if not task.done():
                    task.cancel()

    async def _run_text_lane(self, buffer, lane: asyncio.Queue, mirror_task):
        """Writes the agent's reply to the text channel as it streams."""
        if mirror_task is not None:
            # The transcript goes above the reply; only this lane waits for it.
            try:
                await mirror_task
            except Exception:
                pass
        while True:
            item = await lane.get()
            if item is None:
                return
            kind, payload = item
            if kind == "token":
                await buffer.append_token(payload)
            elif kind == "final":
                await buffer.finalize(
                    final_text=payload.get("text", "") or "",
                    response=payload.get("response")
                )

    async def _run_speech_lane(self, voice_stream, lane: asyncio.Queue, session, turn_id: int, speech: _TurnSpeech):
        """Turns the agent's tokens into speech, in order, while the text lane runs."""
        while True:
            kind, payload = await lane.get()
            if turn_id != self._active_turn:
                return
            if kind == "token":
                for sentence in await voice_stream.add_token(payload):
                    await self._speak(sentence, turn_id, speech)
            elif kind == "bridge":
                if not speech.emitted:
                    await self._play_bridge(payload, turn_id, speech)
            elif kind == "end":
                # Flush remaining text from the voice stream
                for sentence in await voice_stream.flush():
                    await self._speak(sentence, turn_id, speech, label="final chunk")

                # Fallback for short direct answers (e.g. "Done.") that never streamed a token
                final_text = payload
                if not speech.emitted and final_text:
                    spoken = await self.verbalizer.verbalize(final_text, session=session)
                    for sentence in SentenceChunker().split_into_sentences(spoken):
                        await self._speak(sentence, turn_id, speech, label="direct response")
                return

    async def _speak(self, sentence: str, turn_id: int, speech: _TurnSpeech, label: str = "chunk"):
        """Synthesizes one chunk and queues it for playback."""
        if turn_id != self._active_turn:
            return
        speech.emitted = True
        speech.trace.mark("first_chunk")
        print(f"[VoiceManager:{self.agent_id}] 🔊 Synthesizing {label}: '{sentence[:60]}...'")
        tts_file = await self.tts_engine.synthesize_to_file(sentence)
        if tts_file:
            speech.trace.mark("tts_first_byte")
        if tts_file and turn_id == self._active_turn and self.voice_client and self.voice_client.is_connected():
            speech.queued_audio = True
            await self.audio_queue.put(tts_file, auto_delete=True, on_start=speech.on_audio_start)

    async def _play_bridge(self, event, turn_id: int, speech: _TurnSpeech):
        """Plays a floor-holding phrase while the first tool runs, if nothing was said yet."""
        tool_name = event.get("tool_name", "")
        tool_args = event.get("tool_args", {})
        voice_name = self.config.get("tts_voice")
        voice_speed = float(self.config.get("tts_speed", 1.0))
        bridge_audio = await self.bridge_manager.get_or_create_bridge_audio(
            self.agent_id, tool_name, tool_args, self.tts_engine, voice=voice_name, speed=voice_speed
        )
        if bridge_audio and turn_id == self._active_turn and self.voice_client and self.voice_client.is_connected():
            speech.emitted = True
            print(f"[VoiceManager:{self.agent_id}] 🌉 Dual-track bridge: Playing floor-holding audio for '{tool_name}'...")
            await self.audio_queue.put(bridge_audio, priority=False, auto_delete=False)

    # --- Room switching -------------------------------------------------------

    def _room_aliases(self) -> Dict[str, str]:
        """Alias -> voice room name, for every room this bot follows the user into.

        A room is known by its own name ("day planning") and by the names and
        ids of the agents that answer in its text channel ("Daisy", "day planner").
        """
        loader = AgentsLoader()
        agent = loader.get_agent(self.agent_id)
        rooms = []
        for room in (self.bot_runner.get_hosted_voice_channels(agent) if agent else []):
            text_channel = re.sub(r"[-_]voice$", "", str(room))
            names = []
            for agent_id in loader.eligible_agents_for_channel(text_channel, exclude_agent_id=self.agent_id):
                names += [(loader.get_agent_config(agent_id) or {}).get("name", ""), agent_id]
            rooms.append((str(room), names))
        return build_room_aliases(rooms)

    def _find_voice_channel(self, user, room_name: str):
        guild = getattr(getattr(self.voice_client, "channel", None), "guild", None) or getattr(user, "guild", None)
        wanted = self.normalize_channel_name(room_name)
        for vc in getattr(guild, "voice_channels", None) or []:
            if vc.name == room_name or self.normalize_channel_name(vc.name) == wanted:
                return vc
        return None

    def _room_greeting(self, room) -> str:
        """What the room says when you arrive: its agent's name, or the room's."""
        loader = AgentsLoader()
        text_channel = re.sub(r"[-_]voice$", "", room.name)
        agent_id = loader.sole_eligible_agent(text_channel, exclude_agent_id=self.agent_id)
        name = (loader.get_agent_config(agent_id) or {}).get("name") if agent_id else None
        return f"{name} here." if name else f"You're in {room_label(room.name)}."

    async def _wait_for_room(self, room, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            vc = self.voice_client
            if vc and vc.is_connected() and getattr(vc.channel, "id", None) == room.id:
                return True
            await asyncio.sleep(0.1)
        return False

    async def _say(self, text: str, turn_id: int):
        """Speaks a fixed line (room switching) outside an agent turn."""
        if turn_id != self._active_turn:
            return
        tts_file = await self.tts_engine.synthesize_to_file(text)
        if tts_file and self.voice_client and self.voice_client.is_connected():
            self.audio_queue.start()
            await self.audio_queue.put(tts_file, auto_delete=True)

    async def _maybe_switch_room(self, user, transcript: str, turn_id: int) -> bool:
        """Moves the user to the room they asked for. True if this was a switch request.

        No model call: the phrase is matched against the known rooms (see
        core/voice/room_switch.py). The bot moves the user's own connection
        (Discord switches their client over), then follows them -- the runner's
        voice-state handler joins any hosted room the user enters, and this
        waits for that, joining directly only if it doesn't happen.
        """
        room_name = match_room(transcript, self._room_aliases())
        if room_name is None:
            return False
        room = self._find_voice_channel(user, room_name)
        label = room_label(room_name)
        if room is None:
            print(f"[VoiceManager:{self.agent_id}] Room switch: '{room_name}' isn't a voice channel here.")
            await self._say(f"I can't find the {label} room.", turn_id)
            return True
        if self.voice_client and getattr(self.voice_client.channel, "id", None) == room.id:
            await self._say(f"You're already in {label}.", turn_id)
            return True

        print(f"[VoiceManager:{self.agent_id}] 🚪 Room switch: moving {getattr(user, 'display_name', user)} to '{room.name}'.")
        try:
            await user.move_to(room)
        except discord.Forbidden:
            await self._say("I can't move you. I need the Move Members permission.", turn_id)
            return True
        except Exception as e:
            print(f"[VoiceManager:{self.agent_id}] Room switch to '{room.name}' failed: {e}")
            await self._say(f"I couldn't move you to {label}.", turn_id)
            return True

        if not await self._wait_for_room(room, ROOM_FOLLOW_TIMEOUT_S):
            await self.join_voice_channel(str(room.id))
        await self._say(self._room_greeting(room), turn_id)
        return True

    def _play_blurp(self):
        """Plays the agent's static blurp cue immediately."""
        if not self.voice_client or not self.voice_client.is_connected():
            return
            
        try:
            if self.voice_client.is_playing():
                self._stop_playback()
            blurp_file = BlurpGenerator.get_blurp_audio(self.config)
            if blurp_file and os.path.exists(blurp_file):
                source = discord.FFmpegPCMAudio(blurp_file, executable=FFMPEG_EXE)
                self.voice_client.play(source)
        except Exception as e:
            print(f"[VoiceManager:{self.agent_id}] Error playing static blurp: {e}")

