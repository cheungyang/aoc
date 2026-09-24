import time
import os
import io
import wave
import struct
import asyncio
from collections import deque
import numpy as np
import onnxruntime as ort
from discord.ext import voice_recv

class UserVADState:
    """Tracks audio buffer and Silero VAD LSTM state for a single user/SSRC."""
    def __init__(self, user=None, ssrc: int = 0):
        self.user = user
        self.ssrc = ssrc
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.resample_fifo = np.array([], dtype=np.float32)
        self.pre_speech_chunks = deque(maxlen=10) # 10 * 36ms = 360ms pre-roll audio
        self.audio_buffer = bytearray() # Stores 16kHz 16-bit mono PCM of active speech
        self.is_speaking = False
        self.silence_chunks = 0
        self.speech_chunks = 0
        self.last_packet_time = time.time()

    def reset_speech(self):
        self.audio_buffer.clear()
        self.pre_speech_chunks.clear()
        self.is_speaking = False
        self.silence_chunks = 0
        self.speech_chunks = 0

class VADSink(voice_recv.AudioSink):
    """
    Discord voice receive AudioSink with built-in pure ONNX Silero VAD.
    Extracts speech per-SSRC and delivers speech events to VoiceManager.
    """

    # How much silence ends an utterance. Every utterance is dispatched as a
    # full agent turn, and natural pauses (a breath, a hesitation before a
    # noun) run well past 400ms -- at that cutoff one question became two
    # turns, each resending the whole prompt and history. 900ms keeps a
    # mid-sentence pause inside the utterance at the cost of a slightly later
    # reply. Per-agent override: `voice_config.vad_silence_ms`.
    DEFAULT_SILENCE_MS = 900

    def __init__(self, voice_manager, loop=None, silence_duration_ms=DEFAULT_SILENCE_MS, min_speech_ms=250):
        self._voice_client = None
        self.user_states: dict[Union[int, str], UserVADState] = {}
        super().__init__()
        self.voice_manager = voice_manager
        if loop is not None:
            self.loop = loop
        else:
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                self.loop = None

        voice_config = getattr(voice_manager, "config", {})
        if not isinstance(voice_config, dict):
            voice_config = {}

        self.silence_duration_ms = int(voice_config.get("vad_silence_ms", silence_duration_ms))
        self.min_speech_ms = int(voice_config.get("vad_min_speech_ms", min_speech_ms))
        self.onset_ms = int(voice_config.get("vad_onset_ms", 180)) # ~5 chunks of 36ms
        self.onset_speech_prob = float(voice_config.get("vad_speech_prob", 0.65))
        self.continue_prob = float(voice_config.get("vad_continue_prob", 0.35))
        
        # Load ONNX Silero VAD model
        import faster_whisper
        asset_dir = os.path.join(os.path.dirname(faster_whisper.__file__), "assets")
        model_path = os.path.join(asset_dir, "silero_vad_v6.onnx")
        if not os.path.exists(model_path):
            model_path = os.path.join(asset_dir, "silero_vad_v5.onnx")
            
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.vad_session = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
        
        # Start watchdog task to detect when Discord stops sending UDP packets on silence
        self._watchdog_task = None
        if self.loop and self.loop.is_running():
            self._watchdog_task = self.loop.create_task(self._watchdog_loop())

    def _finalize_speech_segment(self, state: UserVADState, user, user_name: str):
        """Finalizes active speech buffer and triggers on_speech_finished if above min_speech_ms."""
        total_speech_ms = len(state.audio_buffer) / 32 # 16kHz 16-bit mono = 32 bytes/ms
        if total_speech_ms >= self.min_speech_ms:
            print(f"[VADSink] 🔇 Finalized speech segment ({int(total_speech_ms)}ms) from {user_name}.")
            wav_bytes = self._pcm_to_wav(state.audio_buffer, sample_rate=16000)
            if self.loop and self.loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self.voice_manager.on_speech_finished(user, wav_bytes), self.loop
                )
        state.reset_speech()

    async def _watchdog_loop(self):
        """Watches for stream cutoffs when Discord stops sending UDP packets on silence."""
        cutoff_sec = max(0.35, self.silence_duration_ms / 1000.0)
        while True:
            try:
                await asyncio.sleep(0.08)
                now = time.time()
                for key, state in list(self.user_states.items()):
                    if state.is_speaking and (now - state.last_packet_time >= cutoff_sec):
                        user = state.user
                        user_name = getattr(user, "display_name", str(user)) if user else str(key)
                        self._finalize_speech_segment(state, user, user_name)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    def wants_opus(self) -> bool:
        return False # We want decoded 48kHz 16-bit stereo PCM

    def write(self, user, data):
        """Called by discord-ext-voice-recv with 20ms of 48kHz stereo PCM data."""
        pcm_bytes = data.pcm
        if not pcm_bytes:
            return

        # Resolve SSRC and user
        ssrc = data.packet.ssrc if hasattr(data, "packet") and hasattr(data.packet, "ssrc") and isinstance(data.packet.ssrc, int) else 0
        if user is None and self.voice_client and ssrc:
            user_id = self.voice_client._get_id_from_ssrc(ssrc)
            if user_id and self.voice_client.guild:
                user = self.voice_client.guild.get_member(user_id)

        # Ignore audio packets from bots (including this bot itself)
        if user is not None and getattr(user, "bot", False) is True:
            return
        if self.voice_client and getattr(self.voice_client, "user", None):
            bot_user = self.voice_client.user
            if user is not None and getattr(user, "id", None) == bot_user.id:
                return
            if ssrc and ssrc == self.voice_client._get_ssrc_from_id(bot_user.id):
                return
                
        # Primary key on SSRC (or user.id) to prevent stream fragmentation
        user_id = getattr(user, "id", None)
        user_key = ssrc if ssrc else (user_id if isinstance(user_id, (int, str)) else "default")
        user_name = getattr(user, "display_name", str(user)) if user else (f"Speaker-{ssrc}" if ssrc else "Speaker")

        if user_key not in self.user_states:
            self.user_states[user_key] = UserVADState(user=user, ssrc=ssrc)
            print(f"[VADSink] 🎙️ Audio stream active for: {user_name} (SSRC: {ssrc})")
            
        state = self.user_states[user_key]
        if user is not None:
            state.user = user
        state.last_packet_time = time.time()
        
        # 1. Downsample 48kHz stereo -> 16kHz mono float32
        int16_stereo = np.frombuffer(pcm_bytes, dtype=np.int16)
        if len(int16_stereo) == 0:
            return
            
        int16_stereo = int16_stereo.reshape(-1, 2)
        int16_mono = (int16_stereo[:, 0].astype(np.float32) + int16_stereo[:, 1].astype(np.float32)) / (2.0 * 32768.0)
        
        # Anti-aliased decimation by 3 (48000 / 3 = 16000) using 3-sample box filter
        trim_len = (len(int16_mono) // 3) * 3
        mono_trimmed = int16_mono[:trim_len]
        mono_16k = (mono_trimmed[0::3] + mono_trimmed[1::3] + mono_trimmed[2::3]) / 3.0
        
        state.resample_fifo = np.concatenate([state.resample_fifo, mono_16k])
        
        # 2. Process in 576-sample windows (36ms at 16kHz for Silero VAD v6)
        window_size = 576
        while len(state.resample_fifo) >= window_size:
            chunk = state.resample_fifo[:window_size].reshape(1, window_size)
            state.resample_fifo = state.resample_fifo[window_size:]
            
            # Run VAD
            prob, state.h, state.c = self.vad_session.run(
                None, {"input": chunk, "h": state.h, "c": state.c}
            )
            speech_prob = float(prob[0])
            rms = float(np.sqrt(np.mean(chunk[0] ** 2)))
            
            # 3. State machine with hysteresis and energy gate to reject ambient noise
            chunk_int16 = (chunk[0] * 32767.0).astype(np.int16).tobytes()
            prob_thresh = self.continue_prob if state.is_speaking else self.onset_speech_prob
            is_speech = (speech_prob >= prob_thresh) and (rms > 1e-6)

            
            if is_speech:
                state.speech_chunks += 1
                state.silence_chunks = 0
                
                # Speech confirmed after sustained onset (default ~180ms)
                if not state.is_speaking and (state.speech_chunks * 36 >= self.onset_ms):
                    state.is_speaking = True
                    # Prepend pre-speech buffer so leading consonants/vowels are never clipped
                    for pre_chunk in state.pre_speech_chunks:
                        state.audio_buffer.extend(pre_chunk)
                    state.pre_speech_chunks.clear()
                    
                    print(f"[VADSink] 🗣️ User {user_name} is speaking (prob={speech_prob:.2f}, rms={rms:.3f})...")
                    if self.loop and self.loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            self.voice_manager.on_speech_started(user), self.loop
                        )
                    
                if state.is_speaking:
                    state.audio_buffer.extend(chunk_int16)
                else:
                    state.pre_speech_chunks.append(chunk_int16)
                    
            else:
                # Silence / non-speech
                if state.is_speaking:
                    state.audio_buffer.extend(chunk_int16)
                    state.silence_chunks += 1
                    silence_ms = state.silence_chunks * 36
                    if silence_ms >= self.silence_duration_ms:
                        self._finalize_speech_segment(state, user, user_name)
                else:
                    state.speech_chunks = 0
                    state.pre_speech_chunks.append(chunk_int16)

    def _pcm_to_wav(self, pcm_data: bytearray, sample_rate=16000) -> bytes:
        """Converts raw 16-bit mono PCM into a normalized standard WAV byte stream."""
        if not pcm_data:
            return b""

        audio_np = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32)
        peak = float(np.max(np.abs(audio_np))) if len(audio_np) > 0 else 0.0
        if peak > 50.0:
            scale = min(28000.0 / peak, 8.0) # Boost quiet signals up to 8x
            audio_np = np.clip(audio_np * scale, -32768.0, 32767.0)

        normalized_pcm = audio_np.astype(np.int16).tobytes()

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1) # Mono
            wav_file.setsampwidth(2) # 16-bit
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(normalized_pcm)
        buffer.seek(0)
        wav_bytes = buffer.getvalue()

        # Save debug recording if enabled
        if os.environ.get("DEBUG_VOICE"):
            try:
                os.makedirs("assets/sounds", exist_ok=True)
                with open("assets/sounds/debug_last_input.wav", "wb") as f:
                    f.write(wav_bytes)
            except Exception:
                pass

        return wav_bytes

    def cleanup(self):
        if hasattr(self, "_watchdog_task") and self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if hasattr(self, "user_states"):
            self.user_states.clear()

