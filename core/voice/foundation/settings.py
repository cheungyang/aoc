"""Voice settings shared by every agent, in one place.

Per-agent choices -- which voice an agent speaks in -- live in that agent's
`agent.json` as a `voice` block:

    "voice": {"name": "en-US-Chirp3-HD-Zephyr", "rate": 1.1}

`name` is required and must be one of `CHIRP3_HD_VOICES` below, with a locale
prefix. `rate` is optional. Every agent you can talk to has a `voice` block,
and no two share one: there is one voice room, and the voice is how you hear
who is answering (tests/core/loaders/test_agent_configs.py checks this). An
agent with no `voice` block -- an internal worker -- speaks in `DEFAULT_VOICE`.

Everything that is the same for all agents belongs here rather than in any
agent.json, so it cannot drift between agents. Operational knobs for the voice
pipeline live here too: speech-to-text, turn taking and text-to-speech below.
"""
import functools
import os
import re
from typing import Optional

from core.util.config import Config

# --- Speech to text --------------------------------------------------------
# "gemini_live": each utterance streams to gemini-3.5-transcribe-live while the
# user is still talking (core/voice/engines/live_stt.py); local Whisper takes over only
# if a live session fails. "whisper": local only, for running without network.
STT_PROVIDER = os.getenv("AOC_STT_PROVIDER", "gemini_live")
STT_LANGUAGE_CODES = ["en-US"]
# SMART drops "um"s and false starts and resolves self-corrections ("Tuesday --
# no, Wednesday"), which is what an agent should act on. VERBATIM keeps them.
STT_MODE = "SMART"
# Agent and room names bias recognition toward them; the docs advise at most
# ~100 terms for best results.
STT_VOCABULARY_LIMIT = 100
STT_CONNECT_TIMEOUT_S = 5.0
# How long after the turn ends to wait for the live transcript before falling
# back to Whisper. Finals normally arrive ~0.4 s after the segment ends.
STT_FINAL_TIMEOUT_S = 2.0
# Silence after which the open speech segment is finalized speculatively, so its
# transcript is usually ready by the time the end-of-turn silence has elapsed.
# Disabled (None): mid-sentence pauses while collecting thoughts otherwise send
# ACTIVITY_END, causing SMART mode to punctuate incomplete clauses and split
# acoustic context across segments.
STT_EARLY_END_MS = None
# Whisper model for the fallback (and for STT_PROVIDER="whisper"). The same
# variable selects what scripts/provision_assets.py caches into the image.
WHISPER_MODEL = os.getenv("AOC_STT_MODEL", "base.en")

# --- Turn taking -----------------------------------------------------------
# How long a silence ends the turn depends on what was said so far
# (core/voice/listening/turn_taking.py): a finished sentence ends quickly, a trailing
# "and" or "um" waits longer, and no words yet keeps the old fixed wait.
END_OF_TURN_COMPLETE_MS = 800
END_OF_TURN_DEFAULT_MS = 900
END_OF_TURN_CONTINUING_MS = 1200
# Barge-in is two-stage: speech over the agent first lowers its volume to this,
# and stops it only once the words confirm an interruption (a stop phrase, or
# at least this many words that aren't just "okay" or "mm-hmm").
BARGE_IN_DUCK_VOLUME = 0.25
BARGE_IN_MIN_WORDS = 2
# A question the agent never got to answer is prepended to the next utterance,
# unless it is older than this.
UNANSWERED_MAX_AGE_S = 90

# --- Hearing only you (Part 4) ---------------------------------------------
# The voiceprint gate (core/voice/listening/speaker_gate.py) scores how much
# an utterance sounds like your enrolled voice. `voice_config.speaker_gate`
# picks the mode per voice session: "off", "shadow" (score and record every
# utterance, change nothing) or "enforce" (not built yet: until the thresholds
# below are set from your drives, it behaves as "shadow").
SPEAKER_GATE_MODES = ("off", "shadow", "enforce")
# A WeSpeaker model trained on VoxCeleb, as exported by sherpa-onnx. CAM++ is
# about twice as fast as ResNet34 for the same separation; time both on the NAS
# with scripts/speaker_gate_bench.py.
SPEAKER_MODEL_FILE = os.getenv("AOC_SPEAKER_MODEL", "wespeaker_en_voxceleb_CAM++.onnx")
SPEAKER_MODEL_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
# Cosine similarity to your voiceprint: at or above ACCEPT it's you, at or
# below REJECT it isn't, in between the gate keeps listening. Provisional, from
# synthetic voices; they're replaced by values set from your drives (phase 4.4).
SPEAKER_ACCEPT = 0.60
SPEAKER_REJECT = 0.40
# The first verdict is on this much audio, then again every step until decided.
SPEAKER_FIRST_S = 1.0
SPEAKER_STEP_S = 0.5
# A score this close to a threshold is "borderline": its audio is kept for review.
SPEAKER_BORDERLINE = 0.10
# The audio of would-be rejections is kept this long, for `!gate review`.
GATE_AUDIO_KEEP_DAYS = 14

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def speaker_model_path() -> Optional[str]:
    """Where the speaker model is: the image's asset cache, else the dev cache
    under sessions/models (scripts/provision_assets.py fills both). None if absent."""
    candidates = [
        os.path.join(os.environ.get("AOC_ASSET_ROOT", "/opt/aoc"), "models", SPEAKER_MODEL_FILE),
        os.path.join(_PROJECT_ROOT, "sessions", "models", SPEAKER_MODEL_FILE),
    ]
    return next((path for path in candidates if os.path.isfile(path)), None)


def voice_data_dir() -> str:
    """Your voiceprint and the kept clips: beside memory.db, on this machine only
    (sessions/ is never committed or put in an image). Looked up at call time,
    so anything that repoints memory.db (the test suite) repoints this."""
    return os.environ.get("AOC_VOICE_DATA_DIR") or os.path.join(
        os.path.dirname(Config().memory_db_path), "voice")

# --- Text to speech --------------------------------------------------------
# "chirp3_hd": Cloud Text-to-Speech, streamed. Each stretch of speech is one
# bidirectional stream, fed a sentence at a time as the agent writes it and
# played as its audio arrives (core/voice/engines/chirp_tts.py). Chirp starts a
# sentence's audio ~150 ms after the sentence ends, and not before: a clause
# sent early waits for the rest of its sentence. edge-tts takes over
# when Chirp can't run here -- no GOOGLE_TTS_API_KEY, or a gRPC client that
# won't load on this CPU -- and for a stream that fails before its first audio.
# "edge": edge-tts only.
TTS_PROVIDER = os.getenv("AOC_TTS_PROVIDER", "chirp3_hd")
# For agents whose `voice` block sets no `rate`. Chirp 3: HD's own pace is 1.0.
DEFAULT_SPEAKING_RATE = 1.2
# Discord plays 48 kHz audio, and Chirp synthesizes at that rate when asked,
# so nothing is resampled here.
TTS_SAMPLE_RATE = 48_000
# A stream still open after this long is cut off.
TTS_STREAM_TIMEOUT_S = 300
# The fallback's voices, by the gender of the agent's Chirp voice.
EDGE_FALLBACK_VOICES = {"female": "en-US-AriaNeural", "male": "en-US-GuyNeural"}


@functools.lru_cache(maxsize=None)
def ffmpeg_exe() -> str:
    """FFmpeg, for the audio cues and decoding the fallback's MP3 (Chirp's PCM needs none)."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"

# Chirp 3: HD voices, keyed by the name that follows the locale prefix
# (`en-US-Chirp3-HD-<name>`). Gender is from Google Cloud's Chirp 3: HD voice
# table; the one-word style is Google's own description of the same voice in
# the Gemini speech-generation docs. Both checked 2026-10-15. The style is
# what to audition first, not a guarantee of how a voice suits an agent.
CHIRP3_HD_VOICES = {
    "Achernar": ("female", "soft"),
    "Achird": ("male", "friendly"),
    "Algenib": ("male", "gravelly"),
    "Algieba": ("male", "smooth"),
    "Alnilam": ("male", "firm"),
    "Aoede": ("female", "breezy"),
    "Autonoe": ("female", "bright"),
    "Callirrhoe": ("female", "easy-going"),
    "Charon": ("male", "informative"),
    "Despina": ("female", "smooth"),
    "Enceladus": ("male", "breathy"),
    "Erinome": ("female", "clear"),
    "Fenrir": ("male", "excitable"),
    "Gacrux": ("female", "mature"),
    "Iapetus": ("male", "clear"),
    "Kore": ("female", "firm"),
    "Laomedeia": ("female", "upbeat"),
    "Leda": ("female", "youthful"),
    "Orus": ("male", "firm"),
    "Pulcherrima": ("female", "forward"),
    "Puck": ("male", "upbeat"),
    "Rasalgethi": ("male", "informative"),
    "Sadachbia": ("male", "lively"),
    "Sadaltager": ("male", "knowledgeable"),
    "Schedar": ("male", "even"),
    "Sulafat": ("female", "warm"),
    "Umbriel": ("male", "easy-going"),
    "Vindemiatrix": ("female", "gentle"),
    "Zephyr": ("female", "bright"),
    "Zubenelgenubi": ("male", "casual"),
}

# The voice of every agent without a `voice` block.
DEFAULT_VOICE = "en-US-Chirp3-HD-Aoede"

# The `voice` block's shape and rate range are checked when an agent loads
# (core/loaders/agent_schema.py); the config tests check names against
# CHIRP3_HD_VOICES with `chirp3_voice`.

_VOICE_NAME = re.compile(r"^(?P<locale>[a-z]{2,3}-[A-Z]{2})-Chirp3-HD-(?P<voice>[A-Za-z]+)$")


def chirp3_voice(name: str) -> Optional[str]:
    """The catalog key in a full voice name, or None if it isn't a known Chirp 3: HD voice.

    `en-US-Chirp3-HD-Zephyr` -> `Zephyr`. A misspelt voice returns None rather
    than passing through: Cloud TTS would reject it only at synthesis time,
    mid-turn, which is the worst moment to find out.
    """
    match = _VOICE_NAME.match(name or "")
    if not match or match.group("voice") not in CHIRP3_HD_VOICES:
        return None
    return match.group("voice")
