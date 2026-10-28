"""The voiceprint gate: does an utterance sound like you?

    the model     a speaker-embedding network (WeSpeaker, trained on VoxCeleb),
                  run with onnxruntime -- already here for Silero VAD -- on
                  80-dim Kaldi fbank features computed in numpy, so no new
                  native wheel has to run on the NAS
    voiceprint    your voice as one embedding: the average of your four
                  `!enroll` readings, with your Discord account. On this
                  machine only, never committed or sent anywhere.
    a verdict     YOU, NOT_YOU or UNDECIDED: the cosine similarity to your
                  voiceprint on the utterance's first second, then every half
                  second until it's decided (SPEAKER_ACCEPT / SPEAKER_REJECT)
    voicing       the share of sounding 30 ms frames that have a pitch:
                  0.8 or more for talking, a quarter or less for a whisper (on
                  synthetic voices), whatever the volume or microphone.
                  Recorded only: nothing acts on it.

Only the speech is scored (`Utterance.speech_pcm`): not the VAD's pre-roll,
nor the end-of-turn silence. Modes, per voice session
(`voice_config.speaker_gate`):

    off       nothing: no scoring, and no owner filter
    shadow    every finished utterance is scored on a worker thread and
              recorded (core/storage/speaker_gate_log.py), with nothing
              you hear changed
    enforce   counts as "shadow": nothing is blocked

Outside "off", only your account is heard at all (the VAD sink's owner
filter): `voice_config.owner_user_ids`, else the account you enrolled from.
"""
import asyncio
import json
import os
import shutil
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Any, Awaitable, Callable, List, Optional, Sequence, Set, Tuple

import numpy as np

from core.storage.speaker_gate_log import Effect, GateLog, GateRecord, Mode
from core.voice.foundation import settings
from core.voice.foundation.utterance import BYTES_PER_MS, SAMPLE_RATE, Utterance

# --- features: 80-dim log mel fbank, Kaldi-compatible -----------------------------
# What WeSpeaker was trained on: 25 ms frames every 10 ms, a Hamming window, no
# dither, normalized by the utterance's mean. Matches kaldi-native-fbank to 2e-4.
FRAME_LENGTH = 400
FRAME_SHIFT = 160
FFT_SIZE = 512
NUM_MEL_BINS = 80
LOW_FREQ = 20.0
PREEMPHASIS = 0.97
# Less audio than this gives no usable embedding.
MIN_EMBED_MS = 500


def _mel(freq):
    return 1127.0 * np.log(1.0 + np.asarray(freq, dtype=np.float64) / 700.0)


@lru_cache(maxsize=1)
def mel_banks() -> np.ndarray:
    """(80, 257) triangular filters over the power spectrum, built as Kaldi builds them."""
    num_fft_bins = FFT_SIZE // 2
    mel_low, mel_high = _mel(LOW_FREQ), _mel(SAMPLE_RATE / 2.0)
    delta = (mel_high - mel_low) / (NUM_MEL_BINS + 1)
    mels = _mel((SAMPLE_RATE / FFT_SIZE) * np.arange(num_fft_bins))
    left = mel_low + np.arange(NUM_MEL_BINS)[:, None] * delta
    center, right = left + delta, left + 2 * delta
    weights = np.maximum(0.0, np.minimum((mels - left) / (center - left), (right - mels) / (right - center)))
    return np.pad(weights, ((0, 0), (0, 1)))  # the Nyquist bin gets no weight


@lru_cache(maxsize=1)
def _hamming() -> np.ndarray:
    n = np.arange(FRAME_LENGTH)
    return 0.54 - 0.46 * np.cos(2 * np.pi * n / (FRAME_LENGTH - 1))


def fbank(samples: np.ndarray) -> np.ndarray:
    """(frames, 80) log mel energies of 16 kHz samples, framed as Kaldi's snip_edges does."""
    samples = np.asarray(samples, dtype=np.float64)
    if len(samples) < FRAME_LENGTH:
        return np.zeros((0, NUM_MEL_BINS), dtype=np.float32)
    count = 1 + (len(samples) - FRAME_LENGTH) // FRAME_SHIFT
    frames = samples[np.arange(FRAME_LENGTH)[None, :] + FRAME_SHIFT * np.arange(count)[:, None]]
    frames = frames - frames.mean(axis=1, keepdims=True)
    frames = np.concatenate([frames[:, :1] * (1 - PREEMPHASIS), frames[:, 1:] - PREEMPHASIS * frames[:, :-1]],
                            axis=1)
    power = np.abs(np.fft.rfft(frames * _hamming(), n=FFT_SIZE)) ** 2
    return np.log(np.maximum(power @ mel_banks().T, np.finfo(np.float32).eps)).astype(np.float32)


# --- voicing: a whisper has no pitch ---------------------------------------------
VOICING_FRAME = 480                                         # 30 ms
_MIN_LAG, _MAX_LAG = SAMPLE_RATE // 400, SAMPLE_RATE // 70  # a pitch between 70 and 400 Hz
VOICED_CORRELATION = 0.5
# Frames this much quieter than the utterance's loud ones are pauses, not speech.
QUIET_FRACTION = 0.1


def voiced_share(samples: np.ndarray) -> float:
    """The share of an utterance's sounding 30 ms frames that have a pitch:
    roughly half or more for talking, near 0 for a whisper. Loudness doesn't
    matter, only the utterance's own frames relative to each other."""
    samples = np.asarray(samples, dtype=np.float64)
    count = len(samples) // VOICING_FRAME
    if count == 0:
        return 0.0
    frames = samples[: count * VOICING_FRAME].reshape(count, VOICING_FRAME)
    frames = frames - frames.mean(axis=1, keepdims=True)
    energy = np.sqrt(np.mean(frames ** 2, axis=1))
    sounding = energy > max(float(np.percentile(energy, 90)) * QUIET_FRACTION, 1e-9)
    if not sounding.any():
        return 0.0
    spectrum = np.fft.rfft(frames[sounding], n=2 * VOICING_FRAME, axis=1)
    autocorrelation = np.fft.irfft(np.abs(spectrum) ** 2, axis=1)[:, :VOICING_FRAME]
    lags = np.arange(_MIN_LAG, _MAX_LAG + 1)
    # Unbiased: each lag averaged over the samples it actually overlaps.
    normalized = (autocorrelation[:, lags] / (VOICING_FRAME - lags)) / (autocorrelation[:, :1] / VOICING_FRAME + 1e-12)
    return float(np.mean(normalized.max(axis=1) >= VOICED_CORRELATION))


# --- the model -------------------------------------------------------------------

class SpeakerModel:
    """The speaker-embedding network, loaded on first use."""

    def __init__(self, path: str):
        self.path = path
        self.name = os.path.basename(path)
        self._session = None
        self._scale = 1.0
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._session is None:
                import onnxruntime as ort

                options = ort.SessionOptions()
                options.intra_op_num_threads = 1
                options.inter_op_num_threads = 1
                session = ort.InferenceSession(self.path, sess_options=options, providers=["CPUExecutionProvider"])
                meta = session.get_modelmeta().custom_metadata_map
                # normalize_samples=0 (WeSpeaker): samples on the 16-bit scale, as our PCM already is.
                self._scale = 1.0 / 32768.0 if meta.get("normalize_samples") == "1" else 1.0
                self._session = session
        return self._session

    def embed(self, pcm: bytes) -> Optional[np.ndarray]:
        """The unit-length embedding of 16 kHz 16-bit mono PCM; None if it's too short."""
        if len(pcm) < MIN_EMBED_MS * BYTES_PER_MS:
            return None
        session = self._load()
        samples = np.frombuffer(bytes(pcm), dtype=np.int16).astype(np.float32) * self._scale
        feats = fbank(samples)
        feats = feats - feats.mean(axis=0, keepdims=True)
        embedding = session.run(None, {"feats": feats[None, :, :]})[0][0].astype(np.float64)
        norm = float(np.linalg.norm(embedding))
        return embedding / norm if norm else None


# --- your voiceprint ---------------------------------------------------------------

def voiceprint_path() -> str:
    return os.path.join(settings.voice_data_dir(), "voiceprint.json")


def clips_dir() -> str:
    """The audio of would-be rejections and borderline utterances, kept for review."""
    return os.path.join(settings.voice_data_dir(), "gate_audio")


@dataclass
class Voiceprint:
    embedding: List[float]
    model: str             # the speaker model it was enrolled with: another one can't compare
    enrolled_at: float
    readings: int
    consistency: float     # the lowest similarity between two readings
    user_id: Optional[str] = None

    def similarity(self, embedding: np.ndarray) -> float:
        return float(np.dot(np.asarray(self.embedding, dtype=np.float64), embedding))

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        partial = path + ".part"
        with open(partial, "w") as f:
            json.dump(asdict(self), f)
        os.chmod(partial, 0o600)
        os.replace(partial, path)

    @classmethod
    def load(cls, path: str) -> Optional["Voiceprint"]:
        try:
            with open(path) as f:
                return cls(**json.load(f))
        except FileNotFoundError:
            return None
        except Exception as e:
            print(f"[SpeakerGate] Could not read the voiceprint at {path}: {e}")
            return None


# --- verdicts ---------------------------------------------------------------------

class Verdict(str, Enum):
    YOU = "YOU"
    NOT_YOU = "NOT_YOU"
    UNDECIDED = "UNDECIDED"


def judge(score: Optional[float]) -> Verdict:
    if score is None:
        return Verdict.UNDECIDED
    if score >= settings.SPEAKER_ACCEPT:
        return Verdict.YOU
    if score <= settings.SPEAKER_REJECT:
        return Verdict.NOT_YOU
    return Verdict.UNDECIDED


def borderline(verdict: Verdict, score: Optional[float]) -> bool:
    """Worth a human check: not called yours, or yours by a thin margin."""
    return verdict is not Verdict.YOU or score is None or score < settings.SPEAKER_ACCEPT + settings.SPEAKER_BORDERLINE


@dataclass
class Assessment:
    verdict: Verdict
    score: Optional[float]               # when it was decided, else the last step's
    decided_after_ms: Optional[int]      # the audio it took; None if it never decided
    scores: List[Tuple[int, float]] = field(default_factory=list)
    score_full: Optional[float] = None   # over the whole utterance
    voiced_share: float = 0.0
    compute_ms: int = 0                  # scoring up to the verdict, as it would run live

    def summary(self) -> dict:
        return {"verdict": self.verdict.value, "score": None if self.score is None else round(self.score, 3),
                "decided_ms": self.decided_after_ms, "voiced": round(self.voiced_share, 2),
                "compute_ms": self.compute_ms}


def assess(model: SpeakerModel, voiceprint: Voiceprint, pcm: bytes) -> Assessment:
    """How the gate judges `pcm`, as it would live: the first second, then
    every half second until it's decided. For the record, also the whole
    utterance's score and its voicing."""
    total_ms = int(len(pcm) / BYTES_PER_MS)
    steps, step = [], int(settings.SPEAKER_FIRST_S * 1000)
    while step < total_ms:
        steps.append(step)
        step += int(settings.SPEAKER_STEP_S * 1000)
    steps.append(total_ms)

    started = time.perf_counter()
    scores: List[Tuple[int, float]] = []
    verdict, score, decided_after = Verdict.UNDECIDED, None, None
    for step in steps:
        embedding = model.embed(pcm[: step * BYTES_PER_MS])
        if embedding is None:
            continue
        score = voiceprint.similarity(embedding)
        scores.append((step, round(score, 4)))
        verdict = judge(score)
        if verdict is not Verdict.UNDECIDED:
            decided_after = step
            break
    compute_ms = int((time.perf_counter() - started) * 1000)

    score_full = scores[-1][1] if scores and scores[-1][0] == total_ms else None
    if score_full is None:
        embedding = model.embed(pcm)
        score_full = round(voiceprint.similarity(embedding), 4) if embedding is not None else None
    voiced = voiced_share(np.frombuffer(bytes(pcm), dtype=np.int16))
    return Assessment(verdict, score, decided_after, scores, score_full, voiced, compute_ms)


# --- the gate ---------------------------------------------------------------------

Transcribe = Callable[[Utterance], Awaitable[Any]]


class SpeakerGate:
    """The voiceprint gate of one voice room."""

    def __init__(self, transcribe: Optional[Transcribe] = None, log: Optional[GateLog] = None,
                 model: Optional[SpeakerModel] = None):
        self.mode = "off"
        self.log = log or GateLog()
        # voice_config.owner_user_ids: the accounts heard, if set.
        self.owner_user_ids: Set[str] = set()
        # A `!enroll` in progress (enrollment.py): its readings aren't scored.
        self.enrollment = None
        self._transcribe = transcribe
        self._model = model
        self._voiceprint: Optional[Voiceprint] = None
        self._voiceprint_read = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="speaker-gate")
        self._tasks = set()

    def _log(self, message: str) -> None:
        print(f"[SpeakerGate] {message}")

    # --- configuration -----------------------------------------------------------

    def configure(self, voice_config: Optional[dict]) -> str:
        """This voice session's mode and owners, from voice_config (`speaker_gate`,
        `owner_user_ids`). Read on every join, so a change needs no restart."""
        config = voice_config if isinstance(voice_config, dict) else {}
        mode = str(config.get("speaker_gate") or "off").strip().lower()
        if mode not in settings.SPEAKER_GATE_MODES:
            self._log(f"Unknown speaker_gate mode {mode!r}; the gate is off.")
            mode = "off"
        if mode == "enforce":
            self._log("enforce blocks nothing: scoring and recording as in shadow mode.")
        self.mode = mode
        owners = config.get("owner_user_ids")
        self.owner_user_ids = {str(o) for o in owners if str(o).strip()} if isinstance(owners, list) else set()
        self._voiceprint_read = False  # enrolled or reset elsewhere since: read it again
        if self.scoring():
            self._executor.submit(self._warm_up)
        return mode

    def _warm_up(self) -> None:
        """Loads the model ahead of the first utterance, so its scoring time is a real one."""
        try:
            self.model._load()
        except Exception as e:
            self._log(f"Could not load the speaker model: {type(e).__name__}: {e}")

    @property
    def model(self) -> Optional[SpeakerModel]:
        if self._model is None:
            path = settings.speaker_model_path()
            if path:
                self._model = SpeakerModel(path)
        return self._model

    def saved_voiceprint(self) -> Optional[Voiceprint]:
        """Your voiceprint as saved, whichever model it was enrolled with."""
        if not self._voiceprint_read:
            self._voiceprint = Voiceprint.load(voiceprint_path())
            self._voiceprint_read = True
        return self._voiceprint

    @property
    def voiceprint(self) -> Optional[Voiceprint]:
        """Your voiceprint, if it was enrolled with the model in use."""
        stored, model = self.saved_voiceprint(), self.model
        if stored is not None and model is not None and stored.model != model.name:
            return None
        return stored

    def owner_ids(self) -> Set[str]:
        """Whose audio is heard: voice_config.owner_user_ids, else the
        account your voiceprint was enrolled from. Empty means everyone's, and
        always when the gate is off. Called by the VAD sink for every packet."""
        if self.mode == "off":
            return set()
        if self.owner_user_ids:
            return self.owner_user_ids
        stored = self.saved_voiceprint()
        return {stored.user_id} if stored is not None and stored.user_id else set()

    def scoring(self) -> bool:
        return self.mode != "off" and self.model is not None and self.voiceprint is not None

    # --- shadow: score, record, change nothing --------------------------------------

    def observe(self, utterance: Utterance, effect: Effect) -> None:
        """A finished utterance, and what it did: scored in the background and recorded."""
        if self.enrollment is not None or not self.scoring():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._observe(utterance, effect, utterance.speech_pcm(), bytes(utterance.pcm)))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _observe(self, utterance: Utterance, effect: Effect, speech: bytes, pcm: bytes) -> None:
        model, voiceprint = self.model, self.voiceprint
        try:
            assessment = await asyncio.get_running_loop().run_in_executor(
                self._executor, assess, model, voiceprint, speech)
        except Exception as e:
            self._log(f"Could not score utterance #{utterance.id}: {type(e).__name__}: {e}")
            return
        utterance.gate = assessment
        transcript = await self._transcript(utterance)
        user_id = getattr(utterance.user, "id", None)
        record = GateRecord(
            at=time.time(), mode=Mode.ENFORCE if self.mode == "enforce" else Mode.SHADOW, model=model.name,
            duration_ms=int(len(speech) / BYTES_PER_MS), verdict=assessment.verdict.value, score=assessment.score,
            decided_after_ms=assessment.decided_after_ms, scores=assessment.scores,
            score_full=assessment.score_full, compute_ms=assessment.compute_ms,
            voiced_share=round(assessment.voiced_share, 3), over_reply=utterance.ducked, effect=effect,
            user_id=str(user_id) if user_id is not None else None, transcript=transcript,
        )
        score = "-" if assessment.score is None else f"{assessment.score:.2f}"
        self._log(f"🗣️ #{utterance.id} {effect.value}: {assessment.verdict.value} {score} after "
                  f"{assessment.decided_after_ms} ms (voiced {assessment.voiced_share:.2f}, "
                  f"{assessment.compute_ms} ms): {transcript!r}")
        try:
            record_id = await asyncio.to_thread(self.log.add, record)
            if borderline(assessment.verdict, assessment.score):
                # The whole utterance, pre-roll and all: what you'd want to hear to judge it.
                path = await asyncio.to_thread(self._keep_clip, record_id, pcm)
                await asyncio.to_thread(self.log.set_audio, record_id, path)
        except Exception as e:
            self._log(f"Could not record utterance #{utterance.id}: {type(e).__name__}: {e}")

    async def _transcript(self, utterance: Utterance) -> Optional[str]:
        if self._transcribe is None:
            return None
        try:
            transcript = await asyncio.wait_for(self._transcribe(utterance), 15)
        except Exception:
            return None
        return getattr(transcript, "text", None)

    def _keep_clip(self, record_id: int, pcm: bytes) -> str:
        """Saves the utterance's audio for review, and drops clips past their days."""
        directory = clips_dir()
        os.makedirs(directory, exist_ok=True)
        cutoff = time.time() - settings.GATE_AUDIO_KEEP_DAYS * 86400
        for entry in os.scandir(directory):
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                os.remove(entry.path)
        path = os.path.join(directory, f"{record_id}.wav")
        with wave.open(path, "wb") as clip:
            clip.setnchannels(1)
            clip.setsampwidth(2)
            clip.setframerate(SAMPLE_RATE)
            clip.writeframes(pcm)
        return path

    # --- enrollment -----------------------------------------------------------------

    async def enroll(self, utterances: Sequence[Utterance]) -> Voiceprint:
        """Your voiceprint, from your readings: their embeddings averaged, saved, and in use at once."""
        model = self.model
        if model is None:
            raise RuntimeError("the speaker model isn't installed")
        pcms = [u.speech_pcm() for u in utterances]
        embeddings = await asyncio.get_running_loop().run_in_executor(
            self._executor, lambda: [e for e in (model.embed(p) for p in pcms) if e is not None])
        if len(embeddings) < 2:
            raise ValueError("not enough speech in the readings")
        mean = np.mean(embeddings, axis=0)
        mean /= np.linalg.norm(mean)
        similarities = [float(a @ b) for i, a in enumerate(embeddings) for b in embeddings[i + 1:]]
        user = next((u.user for u in utterances if u.user is not None and getattr(u.user, "id", None)), None)
        voiceprint = Voiceprint(
            embedding=[round(float(x), 6) for x in mean], model=model.name, enrolled_at=time.time(),
            readings=len(embeddings), consistency=round(min(similarities), 3),
            user_id=str(user.id) if user is not None else None,
        )
        await asyncio.to_thread(voiceprint.save, voiceprint_path())
        self._voiceprint, self._voiceprint_read = voiceprint, True
        return voiceprint

    def reset(self) -> bool:
        """Deletes your voiceprint and the kept clips. True if there was a voiceprint."""
        try:
            os.remove(voiceprint_path())
            removed = True
        except FileNotFoundError:
            removed = False
        shutil.rmtree(clips_dir(), ignore_errors=True)
        self._voiceprint, self._voiceprint_read = None, True
        return removed
