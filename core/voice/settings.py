"""Voice settings shared by every agent, in one place.

Per-agent choices -- which voice an agent speaks in -- live in that agent's
`agent.json` as a `voice` block:

    "voice": {"name": "en-US-Chirp3-HD-Zephyr", "rate": 1.1}

`name` is required and must be one of `CHIRP3_HD_VOICES` below, with a locale
prefix. `rate` is optional. An agent with no `voice` block speaks in
`DEFAULT_VOICE`; that is deliberate for agents whose names carry no voice of
their own (Concierge, Fitbod, ...), not an omission.

Everything that is the same for all agents belongs here rather than in any
agent.json, so it cannot drift between agents. Operational knobs for the voice
pipeline (providers, speech mode, end-of-turn timing) join this module as the
streaming STT/TTS phases land.
"""
import re
from typing import Optional

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
