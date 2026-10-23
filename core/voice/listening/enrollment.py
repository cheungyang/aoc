"""`!enroll`: your voiceprint, from four sentences read in the voice channel.

Typed in any text channel while you're in the voice channel -- in the car is
best, since its microphone and Bluetooth change how you sound. The bot posts
the sentences there and asks you, out loud, to read them, pausing after each.
Every finished utterance with enough speech is a reading; after the last one
the speaker gate averages them into your voiceprint (speaker_gate.py).

While it runs, what you say is a reading, not a request: VoiceManager hands
each utterance here instead of to an agent. Saying "cancel" or typing
`!enroll cancel` stops it; so do leaving the voice channel, and
ENROLL_TIMEOUT_S without a word.
"""
import time
from typing import Any, Callable, List, Sequence

from core.voice.foundation.utterance import Utterance

# Phonetically varied, and like what you say to the agents anyway.
SENTENCES = (
    "Hey Concierge, what's on my calendar this afternoon, and do I need to prepare anything?",
    "Add oat milk, eggs, spinach and a loaf of sourdough bread to the shopping list for Saturday.",
    "Take me to Daisy, then tell me how long the drive to the office will take this morning.",
    "Remind me at seven thirty to call my brother about the trip to Vancouver next month.",
)
# A reading needs this much speech; anything shorter is asked for again.
MIN_READING_MS = 2000
# An enrollment left this long without a reading is dropped.
ENROLL_TIMEOUT_S = 180
# Readings less alike than this (their lowest pairwise similarity) suggest one
# of them wasn't you, or was buried in noise: you're told to enroll again.
MIN_CONSISTENCY = 0.55

_NUMBERS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")


def _spoken(n: int) -> str:
    return _NUMBERS[n] if 0 <= n < len(_NUMBERS) else str(n)


class Enrollment:
    """One `!enroll` in progress."""

    def __init__(self, channel: Any = None, sentences: Sequence[str] = SENTENCES,
                 clock: Callable[[], float] = time.monotonic):
        self.channel = channel  # the text channel `!enroll` was typed in
        self.sentences = list(sentences)
        self.readings: List[Utterance] = []
        self._clock = clock
        self._last_heard = clock()

    @property
    def done(self) -> bool:
        return len(self.readings) >= len(self.sentences)

    def remaining(self) -> int:
        return max(0, len(self.sentences) - len(self.readings))

    def expired(self) -> bool:
        return self._clock() - self._last_heard > ENROLL_TIMEOUT_S

    def take(self, utterance: Utterance) -> bool:
        """Keeps `utterance` as the next reading, if it has enough speech."""
        self._last_heard = self._clock()
        if self.done or utterance.speech_ms() < MIN_READING_MS:
            return False
        self.readings.append(utterance)
        return True

    # --- what's posted and said ------------------------------------------------

    def instructions(self) -> str:
        """Posted where `!enroll` was typed."""
        lines = ["🎙️ **Voiceprint enrollment.** Read these out loud, one at a time, pausing after each:"]
        lines += [f"{i}. {sentence}" for i, sentence in enumerate(self.sentences, 1)]
        lines.append('If I ask for more, read any of them again. Say "cancel" or type `!enroll cancel` to stop.')
        return "\n".join(lines)

    def opening_line(self) -> str:
        return (f"Let's take your voiceprint. Read the {_spoken(len(self.sentences))} sentences "
                f"in the chat, pausing after each.")

    def progress_line(self) -> str:
        return f"Got it. {_spoken(self.remaining()).capitalize()} more."

    def retry_line(self) -> str:
        return f"I missed that. Sentence {_spoken(len(self.readings) + 1)}, please."


def saved_message(voiceprint: Any, mode: str) -> str:
    """Posted once the voiceprint is saved."""
    text = (f"🎙️ Voiceprint saved from {voiceprint.readings} readings "
            f"(consistency {voiceprint.consistency:.2f}).")
    if voiceprint.consistency < MIN_CONSISTENCY:
        text += (" ⚠️ The readings sound quite different from each other: one may not have been you, "
                 "or noise got in. Type `!enroll` to take it again.")
    if mode == "off":
        text += " The gate is off: set `voice_config.speaker_gate` to `shadow` to start recording scores."
    else:
        text += f" The gate is in `{mode}` mode."
    return text
