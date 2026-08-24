import os
import io
import re
import asyncio
import discord
from discord.ext import voice_recv
from .vad_sink import VADSink
from .stt_engine import STTEngine
from .tts_engine import TTSEngine
from .blurp_generator import BlurpGenerator
from core.loaders.agents_loader import AgentsLoader
from core.agent.session_manager import SessionManager

# Ensure FFmpeg executable is located
try:
    import imageio_ffmpeg
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_EXE = "ffmpeg"

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
        
        # Initialize STT and TTS engines
        stt_model = self.config.get("stt_model", "base.en")
        tts_voice = self.config.get("tts_voice", "en-US-JennyNeural")
        tts_speed = float(self.config.get("tts_speed", 1.0))
        
        self.stt_engine = STTEngine(model_size=stt_model)
        self.tts_engine = TTSEngine(default_voice=tts_voice, default_speed=tts_speed)
        
        self._is_processing = False
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
        3. Falls back to the voice channel's built-in text chat.
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

        # Fallback to the voice channel itself (Discord voice channels support text chat)
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
        """Joins a Discord voice channel and starts listening with VADSink."""
        if text_channel is not None:
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
            
        except (asyncio.TimeoutError, TimeoutError):
            print(f"[VoiceManager:{self.agent_id}] Failed to connect to voice channel '{channel_name}': Connection timed out (UDP/voice handshake timeout).")
            await self._cleanup_failed_connection(target_channel)
            return False
        except discord.errors.ClientException as e:
            print(f"[VoiceManager:{self.agent_id}] Failed to connect to voice channel '{channel_name}': Discord client error: {e}")
            await self._cleanup_failed_connection(target_channel)
            return False
        except (discord.errors.ConnectionClosed, discord.errors.GatewayNotFound) as e:
            print(f"[VoiceManager:{self.agent_id}] Failed to connect to voice channel '{channel_name}': Voice gateway connection closed ({e}).")
            await self._cleanup_failed_connection(target_channel)
            return False
        except (OSError, ConnectionError) as e:
            print(f"[VoiceManager:{self.agent_id}] Failed to connect to voice channel '{channel_name}': Network error ({e}).")
            await self._cleanup_failed_connection(target_channel)
            return False
        except asyncio.CancelledError:
            print(f"[VoiceManager:{self.agent_id}] Voice connection to '{channel_name}' was cancelled.")
            await self._cleanup_failed_connection(target_channel)
            return False
        except Exception as e:
            err_msg = str(e).strip() or type(e).__name__
            print(f"[VoiceManager:{self.agent_id}] Failed to connect to voice channel '{channel_name}': {err_msg}")
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
        if not self.voice_client:
            return
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
        if self.voice_client and self.voice_client.is_playing():
            user_name = getattr(user, "display_name", str(user))
            print(f"[VoiceManager:{self.agent_id}] 🛑 User {user_name} spoke during playback. Halting audio (Barge-in).")
            self._stop_playback()

        # 2. Cancel prior in-flight generation/synthesis task
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            self._current_task = None

    async def on_speech_finished(self, user, wav_bytes: bytes):
        """Called when user finishes speaking. Spawns processing task for this turn."""
        if user and getattr(user, "bot", False) is True:
            return
        if self.voice_client and getattr(self.voice_client, "user", None) and getattr(user, "id", None) == getattr(self.voice_client.user, "id", None):
            return

        # Cancel any previous task
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()

        turn_id = self._active_turn
        self._current_task = asyncio.create_task(self._process_speech_turn(user, wav_bytes, turn_id))

    async def _process_speech_turn(self, user, wav_bytes: bytes, turn_id: int):
        """Processes a speech turn through STT, Agent, and TTS."""
        async with self._lock:
            try:
                # 1. Instantly play static acoustic blurp
                if turn_id != self._active_turn:
                    return
                print(f"[VoiceManager:{self.agent_id}] 🔔 Playing signature blurp...")
                self._play_blurp()
                
                # 2. Transcribe speech in background
                print(f"[VoiceManager:{self.agent_id}] ⚡ Transcribing audio with Faster-Whisper...")
                transcript = await self.stt_engine.transcribe(wav_bytes)
                if not transcript:
                    print(f"[VoiceManager:{self.agent_id}] Transcription empty or filtered as noise. Ignoring.")
                    return
                    
                if turn_id != self._active_turn:
                    return

                author_name = getattr(user, "display_name", str(user)) if user else "Speaker"
                print(f"[VoiceManager:{self.agent_id}] 📝 Transcribed prompt from {author_name}: '{transcript}'")
                
                # 3. Resolve the corresponding text channel so context is 100% shared with text
                target_channel = self.resolve_corresponding_text_channel(self.voice_client.channel)
                target_name = getattr(target_channel, "name", str(target_channel))
                
                # Mirror user prompt to the shared text channel
                if self.config.get("post_transcript_to_chat", True) and target_channel:
                    try:
                        await target_channel.send(f"🎤 **{author_name} (Voice):** {transcript}")
                    except Exception as te:
                        print(f"[VoiceManager:{self.agent_id}] Error posting transcript: {te}")
                
                if turn_id != self._active_turn:
                    return

                # 4. Execute LangGraph Agent with source="discord" so it shares the EXACT same session context as typing!
                loader = AgentsLoader()
                agent = loader.get_agent(self.agent_id)
                if not agent:
                    return
                    
                print(f"[VoiceManager:{self.agent_id}] 🤖 Executing LangGraph agent under #{target_name} context...")
                response_text = await agent.execute(
                    content=transcript,
                    source="discord", # Shared context with text channel
                    channel=target_channel
                )
                
                if turn_id != self._active_turn:
                    print(f"[VoiceManager:{self.agent_id}] Discarding response due to newer voice interruption.")
                    return

                print(f"[VoiceManager:{self.agent_id}] 💬 Agent response: '{response_text[:80]}...'")
                
                # 5. Synthesize and speak reply
                if response_text and self.voice_client and self.voice_client.is_connected():
                    print(f"[VoiceManager:{self.agent_id}] 🔊 Synthesizing speech with Edge-TTS...")
                    tts_file = await self.tts_engine.synthesize_to_file(response_text)
                    
                    if turn_id != self._active_turn:
                        if tts_file and os.path.exists(tts_file):
                            os.unlink(tts_file)
                        return

                    if tts_file and os.path.exists(tts_file):
                        print(f"[VoiceManager:{self.agent_id}] 🎧 Streaming audio reply to voice channel...")
                        self._play_audio_file(tts_file)
                        
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"[VoiceManager:{self.agent_id}] Error in voice pipeline: {e}")
                import traceback
                traceback.print_exc()
            finally:
                self._is_processing = False

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

    def _play_audio_file(self, file_path: str):
        """Plays an audio file into the Discord voice channel and removes temp file afterwards."""
        if not self.voice_client or not self.voice_client.is_connected():
            if os.path.exists(file_path):
                os.unlink(file_path)
            return

        def _after_play(error):
            if error:
                print(f"[VoiceManager:{self.agent_id}] Playback error: {error}")
            if os.path.exists(file_path):
                try:
                    os.unlink(file_path)
                except Exception:
                    pass

        try:
            # Stop any playing blurp before speaking
            if self.voice_client.is_playing():
                self._stop_playback()
                
            source = discord.FFmpegPCMAudio(file_path, executable=FFMPEG_EXE)
            self.voice_client.play(source, after=_after_play)
        except Exception as e:
            print(f"[VoiceManager:{self.agent_id}] Error streaming audio: {e}")
            if os.path.exists(file_path):
                os.unlink(file_path)
