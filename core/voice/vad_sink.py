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
    """Tracks audio buffer and Silero VAD LSTM state for a single user."""
    def __init__(self, user=None):
        self.user = user
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
    Extracts speech per-user and delivers speech events to VoiceManager.
    """
    
    def __init__(self, voice_manager, loop=None, silence_duration_ms=400, min_speech_ms=250):
        self._voice_client = None
        self.user_states: dict[int, UserVADState] = {}
        super().__init__()
        self.voice_manager = voice_manager
        if loop is not None:
            self.loop = loop
        else:
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                self.loop = None
        self.silence_duration_ms = silence_duration_ms
        self.min_speech_ms = min_speech_ms
        
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

    async def _watchdog_loop(self):
        """Watches for stream cutoffs when Discord stops sending UDP packets on silence."""
        while True:
            try:
                await asyncio.sleep(0.08)
                now = time.time()
                for key, state in list(self.user_states.items()):
                    if state.is_speaking and (now - state.last_packet_time >= 0.35):
                        total_speech_ms = len(state.audio_buffer) / 32
                        user = state.user
                        user_name = getattr(user, "display_name", str(user)) if user else str(key)
                        
                        if total_speech_ms >= self.min_speech_ms:
                            print(f"[VADSink] 🔇 Stream pause detected. Finalized speech segment ({int(total_speech_ms)}ms) from {user_name}.")
                            wav_bytes = self._pcm_to_wav(state.audio_buffer, sample_rate=16000)
                            if self.loop and self.loop.is_running():
                                asyncio.run_coroutine_threadsafe(
                                    self.voice_manager.on_speech_finished(user, wav_bytes), self.loop
                                )
                        state.reset_speech()
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

        # Attempt to resolve user if None
        ssrc = data.packet.ssrc if hasattr(data, "packet") and hasattr(data.packet, "ssrc") else 0
        if user is None and self.voice_client and ssrc:
            user_id = self.voice_client._get_id_from_ssrc(ssrc)
            if user_id and self.voice_client.guild:
                user = self.voice_client.guild.get_member(user_id)

        # Ignore audio packets from bots (including this bot itself to prevent feedback loops)
        if user is not None:
            if getattr(user, "bot", False) is True:
                return
            if self.voice_client and getattr(self.voice_client, "user", None) and getattr(user, "id", None) == getattr(self.voice_client.user, "id", None):
                return
        elif self.voice_client and getattr(self.voice_client, "user", None) and ssrc:
            bot_ssrc = self.voice_client._get_ssrc_from_id(self.voice_client.user.id)
            if bot_ssrc and ssrc == bot_ssrc:
                return
                
        if user is not None:
            user_key = user.id if hasattr(user, "id") else hash(user)
            user_name = getattr(user, "display_name", str(user))
        else:
            user_key = ssrc or "unknown"
            user_name = f"Speaker-{ssrc}" if ssrc else "Speaker"

        if user_key not in self.user_states:
            self.user_states[user_key] = UserVADState(user=user)
            print(f"[VADSink] 🎙️ Audio stream active for: {user_name} (SSRC: {ssrc})")
            
        state = self.user_states[user_key]
        if user is not None:
            state.user = user
        state.last_packet_time = time.time()
        
        # 1. Downsample 48kHz stereo -> 16kHz mono float32
        # Convert bytes to int16 numpy array
        int16_stereo = np.frombuffer(pcm_bytes, dtype=np.int16)
        if len(int16_stereo) == 0:
            return
            
        # Reshape to (N, 2) and average channels
        int16_stereo = int16_stereo.reshape(-1, 2)
        int16_mono = (int16_stereo[:, 0].astype(np.float32) + int16_stereo[:, 1].astype(np.float32)) / (2.0 * 32768.0)
        
        # Anti-aliased decimation by 3 (48000 / 3 = 16000) using 3-sample box filter
        trim_len = (len(int16_mono) // 3) * 3
        mono_trimmed = int16_mono[:trim_len]
        mono_16k = (mono_trimmed[0::3] + mono_trimmed[1::3] + mono_trimmed[2::3]) / 3.0
        
        # Append to resample FIFO
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
            
            # 3. State machine
            chunk_int16 = (chunk[0] * 32767.0).astype(np.int16).tobytes()
            
            if speech_prob > 0.5:
                state.speech_chunks += 1
                state.silence_chunks = 0
                
                # Check if speech just began
                if not state.is_speaking and (state.speech_chunks * 36 >= 72): # ~72ms of confident speech
                    state.is_speaking = True
                    # Prepend pre-speech buffer so leading consonants/vowels are never clipped
                    for pre_chunk in state.pre_speech_chunks:
                        state.audio_buffer.extend(pre_chunk)
                    state.pre_speech_chunks.clear()
                    
                    print(f"[VADSink] 🗣️ User {user_name} is speaking...")
                    # Notify voice manager for barge-in / interruption
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
                    
                    # Check if silence duration exceeded threshold (e.g. 800ms)
                    silence_ms = state.silence_chunks * 36
                    total_speech_ms = len(state.audio_buffer) / 32 # 16kHz 16-bit = 32 bytes per ms
                    
                    if silence_ms >= self.silence_duration_ms:
                        if total_speech_ms >= self.min_speech_ms:
                            print(f"[VADSink] 🔇 Silence detected. Finalized speech segment ({int(total_speech_ms)}ms) from {user_name}.")
                            # Finalize speech segment and create WAV
                            wav_bytes = self._pcm_to_wav(state.audio_buffer, sample_rate=16000)
                            if self.loop and self.loop.is_running():
                                asyncio.run_coroutine_threadsafe(
                                    self.voice_manager.on_speech_finished(user, wav_bytes), self.loop
                                )
                        state.reset_speech()
                else:
                    state.speech_chunks = 0
                    state.pre_speech_chunks.append(chunk_int16)

    def _pcm_to_wav(self, pcm_data: bytearray, sample_rate=16000) -> bytes:
        """Converts raw 16-bit mono PCM into a normalized standard WAV byte stream."""
        if not pcm_data:
            return b""

        # Normalize audio volume so quiet microphones are loud and clear for Whisper
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
