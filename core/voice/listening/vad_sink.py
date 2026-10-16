import asyncio
import os
import threading
import time
from collections import deque
from typing import Optional, Union

import numpy as np
import onnxruntime as ort
from discord.ext import voice_recv

from core.voice.settings import STT_EARLY_END_MS
from core.voice.listening.turn_taking import end_of_turn_ms
from core.voice.listening.utterance import Utterance

WINDOW_SAMPLES = 576  # 36 ms at 16 kHz: Silero VAD v6's window
WINDOW_MS = 36


class UserVADState:
    """Silero VAD LSTM state, and the utterance in progress, for one user/SSRC."""
    def __init__(self, user=None, ssrc: int = 0):
        self.user = user
        self.ssrc = ssrc
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.resample_fifo = np.array([], dtype=np.float32)
        self.pre_speech_chunks = deque(maxlen=10) # 10 * 36ms = 360ms pre-roll audio
        self.utterance: Optional[Utterance] = None
        self.is_speaking = False
        # The utterance's open transcription segment was ended early in a pause.
        self.paused = False
        self.silence_chunks = 0
        self.speech_chunks = 0
        self.last_packet_time = time.monotonic()
        # time.monotonic() of the last chunk heard as speech: when the user
        # actually stopped talking, before the end-of-turn silence wait.
        self.last_voiced_at = None

    def reset_speech(self):
        self.utterance = None
        self.pre_speech_chunks.clear()
        self.is_speaking = False
        self.paused = False
        self.silence_chunks = 0
        self.speech_chunks = 0
        self.last_voiced_at = None

class VADSink(voice_recv.AudioSink):
    """
    Discord voice receive AudioSink with built-in pure ONNX Silero VAD.

    Each stretch of a user's speech becomes an `Utterance` that is streamed to
    the floor (core/voice/listening/floor.py) while the user is still talking, so
    transcription runs alongside the speech instead of after it:

      * `utterance_opened` at speech onset,
      * `utterance_audio` for every 36 ms window from onset on,
      * `utterance_paused` a moment into each pause (STT_EARLY_END_MS) and
        `utterance_resumed` if speech returns before the turn ends,
      * `utterance_finished` at the end of the turn, or `utterance_discarded`
        if it was too short to be speech.

    They are posted to the event loop in order from this thread (Discord's
    packet reader). How long a silence ends the turn is adaptive: it depends on
    the words transcribed so far (core/voice/listening/turn_taking.py).

    `config` is the agent's voice_config, for its VAD thresholds.
    """

    def __init__(self, floor, loop=None, config: Optional[dict] = None, min_speech_ms=250):
        self._voice_client = None
        self.user_states: dict[Union[int, str], UserVADState] = {}
        super().__init__()
        self.floor = floor
        if loop is not None:
            self.loop = loop
        else:
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                self.loop = None

        voice_config = config if isinstance(config, dict) else {}

        self.min_speech_ms = int(voice_config.get("vad_min_speech_ms", min_speech_ms))
        self.onset_ms = int(voice_config.get("vad_onset_ms", 180)) # ~5 chunks of 36ms
        self.onset_speech_prob = float(voice_config.get("vad_speech_prob", 0.65))
        self.continue_prob = float(voice_config.get("vad_continue_prob", 0.35))
        self.early_end_ms = STT_EARLY_END_MS
        # `write` runs on Discord's packet thread and the watchdog on the event
        # loop; both can end the same turn, so state changes happen under this.
        self._lock = threading.Lock()

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

    def _post(self, fn, *args):
        """Runs `fn(*args)` on the event loop, after everything posted before it."""
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(fn, *args)

    def _end_of_turn_ms(self, state: UserVADState) -> int:
        utterance = state.utterance
        return end_of_turn_ms(utterance.text_so_far() if utterance else "")

    def _open_utterance(self, state: UserVADState, user, user_name: str, speech_prob: float, rms: float):
        """Speech confirmed: start an utterance, with the pre-roll so leading sounds aren't clipped."""
        state.is_speaking = True
        state.paused = False
        utterance = Utterance(user=user, key=state.ssrc or getattr(user, "id", None))
        preroll = b"".join(state.pre_speech_chunks)
        state.pre_speech_chunks.clear()
        utterance.pcm += preroll
        state.utterance = utterance
        print(f"[VADSink] 🗣️ User {user_name} is speaking (prob={speech_prob:.2f}, rms={rms:.3f})...")
        self._post(self.floor.utterance_opened, utterance)
        if preroll:
            self._post(self.floor.utterance_audio, utterance, preroll)

    def _append(self, state: UserVADState, chunk: bytes):
        state.utterance.pcm += chunk
        self._post(self.floor.utterance_audio, state.utterance, chunk)

    def _on_silence(self, state: UserVADState, silence_ms: float, user_name: str):
        """Ends the open segment early, then the turn once the silence is long enough."""
        if state.utterance is None:
            return
        if not state.paused and silence_ms >= self.early_end_ms:
            state.paused = True
            self._post(self.floor.utterance_paused, state.utterance)
        if silence_ms >= self._end_of_turn_ms(state):
            self._finalize_speech_segment(state, user_name, silence_ms)

    def _finalize_speech_segment(self, state: UserVADState, user_name: str, silence_ms: float = 0):
        """Ends the turn: hands the utterance on if it's long enough to be speech."""
        utterance = state.utterance
        if utterance is not None:
            if utterance.duration_ms() >= self.min_speech_ms:
                utterance.speech_end_at = state.last_voiced_at
                print(f"[VADSink] 🔇 Turn ended after {int(silence_ms)}ms of silence "
                      f"({int(utterance.duration_ms())}ms of audio) from {user_name}.")
                self._post(self.floor.utterance_finished, utterance)
            else:
                self._post(self.floor.utterance_discarded, utterance)
        state.reset_speech()

    async def _watchdog_loop(self):
        """Ends turns when Discord stops sending packets: a silent speaker sends
        no frames at all, so the silence windows `write` counts may never come.

        Silence is measured from the last voiced frame, like `write` counts it,
        not from the last packet: Discord sends a few silence frames after the
        speech stops, and counting from those would end every turn late.
        """
        while True:
            try:
                await asyncio.sleep(0.08)
                now = time.monotonic()
                with self._lock:
                    for key, state in list(self.user_states.items()):
                        if state.is_speaking:
                            user = state.user
                            user_name = getattr(user, "display_name", str(user)) if user else str(key)
                            since = state.last_voiced_at or state.last_packet_time
                            self._on_silence(state, (now - since) * 1000, user_name)
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

        with self._lock:
            if user_key not in self.user_states:
                self.user_states[user_key] = UserVADState(user=user, ssrc=ssrc)
                print(f"[VADSink] 🎙️ Audio stream active for: {user_name} (SSRC: {ssrc})")

            state = self.user_states[user_key]
            if user is not None:
                state.user = user
            state.last_packet_time = time.monotonic()

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

            # 2. Process in 36ms windows
            while len(state.resample_fifo) >= WINDOW_SAMPLES:
                chunk = state.resample_fifo[:WINDOW_SAMPLES].reshape(1, WINDOW_SAMPLES)
                state.resample_fifo = state.resample_fifo[WINDOW_SAMPLES:]

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
                    state.last_voiced_at = time.monotonic()

                    # Speech confirmed after sustained onset (default ~180ms)
                    if not state.is_speaking and (state.speech_chunks * WINDOW_MS >= self.onset_ms):
                        self._open_utterance(state, user, user_name, speech_prob, rms)

                    if state.is_speaking:
                        if state.paused:
                            # Back to talking before the turn ended: same utterance.
                            state.paused = False
                            self._post(self.floor.utterance_resumed, state.utterance)
                        self._append(state, chunk_int16)
                    else:
                        state.pre_speech_chunks.append(chunk_int16)

                else:
                    # Silence / non-speech
                    if state.is_speaking:
                        self._append(state, chunk_int16)
                        state.silence_chunks += 1
                        self._on_silence(state, state.silence_chunks * WINDOW_MS, user_name)
                    else:
                        state.speech_chunks = 0
                        state.pre_speech_chunks.append(chunk_int16)

    def cleanup(self):
        if hasattr(self, "_watchdog_task") and self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if hasattr(self, "user_states"):
            with getattr(self, "_lock", None) or threading.Lock():
                for state in self.user_states.values():
                    if state.utterance is not None:
                        self._post(self.floor.utterance_discarded, state.utterance)
                self.user_states.clear()
