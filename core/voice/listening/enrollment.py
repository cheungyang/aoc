"""`!enroll`: your voiceprint, from four sentences read in the voice channel.

Typed in any text channel while you're in the voice channel -- in the car is
best, since its microphone and Bluetooth change how you sound. The bot posts
the sentences there and asks you, out loud, to read them, pausing after each.
Every finished utterance with enough speech is a reading; after the last one
the speaker gate averages them into your voiceprint (speaker_gate.py).

While it runs, what you say is a reading, not a request: VoiceManager hands
each turn's utterances to `EnrollmentFlow` instead of to an agent. Saying
"cancel" or typing `!enroll cancel` stops it; so do leaving the voice
channel, and ENROLL_TIMEOUT_S without a word.
"""
import time
from typing import Any, Callable, List, Optional, Sequence

from core.voice.foundation import settings
from core.voice.foundation.utterance import Utterance
from core.voice.listening.turn_taking import bare_stop

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


class EnrollmentFlow:
    """`!enroll` in the voice room: starts and stops an Enrollment, takes what
    you say as readings while one runs, and saves your voiceprint.

    VoiceManager builds it with what it needs from the room: the speaker gate,
    which holds the enrollment and makes the voiceprint; how to transcribe and
    how to speak in the bot's voice; how to run something beside the turn; and
    how to quiet the room when an enrollment starts.
    """

    def __init__(self, gate: Any, *, transcribe: Callable, say: Callable, spawn: Callable,
                 quiet: Callable[[], None], log: Callable[[str], None]):
        self.gate = gate
        self._transcribe = transcribe  # utterance -> its Transcript
        self._say = say                # text -> said in the bot's own voice
        self._spawn = spawn            # runs a coroutine beside the turn
        self._quiet = quiet            # stops any reply, and forgets what's unanswered
        self._log = log

    def command(self, args: str = "", channel: Any = None, *, in_room: bool) -> str:
        """`!enroll`, typed in `channel`: starts taking your voiceprint; with
        "cancel", stops. `in_room`: you and the bot are in the voice channel.
        Returns what to post back."""
        gate, action = self.gate, (args or "").strip().lower()
        if action in ("cancel", "stop"):
            if gate.enrollment is None:
                return "No enrollment is in progress."
            gate.enrollment = None
            return "Enrollment cancelled."
        if action:
            return "Type `!enroll` to take your voiceprint, or `!enroll cancel` to stop."
        if not in_room:
            return "Join the voice channel first, then type `!enroll` again."
        if gate.model is None:
            return (f"The speaker model `{settings.SPEAKER_MODEL_FILE}` isn't installed: run "
                    f"`scripts/provision_assets.py --stage runtime --only speaker`.")
        enrollment = Enrollment(channel)
        gate.enrollment = enrollment
        self._quiet()
        self._log("🎙️ Voiceprint enrollment started.")
        self._spawn(self._say(enrollment.opening_line()))
        return enrollment.instructions()

    def reset(self) -> None:
        """No enrollment carries over: you left, or a new voice session began."""
        self.gate.enrollment = None

    def active(self) -> Optional[Enrollment]:
        """The enrollment in progress, unless it has timed out."""
        enrollment = self.gate.enrollment
        if enrollment is not None and enrollment.expired():
            self._log("🎙️ Voiceprint enrollment timed out.")
            self.gate.enrollment = enrollment = None
        return enrollment

    async def take(self, enrollment: Enrollment, utterances: List[Utterance]) -> None:
        """What you said during `!enroll`: readings for your voiceprint, not requests.
        They're taken before anything is awaited, so a reading that interrupts
        the bot's "Got it" still counts."""
        if not any([enrollment.take(u) for u in utterances]):
            # Too short for a reading: "cancel", "wait", a word or two, or noise.
            text = (await self._transcribe(utterances[-1])).text.strip()
            stop = bare_stop(text)
            if stop == "cancel":
                self.gate.enrollment = None
                self._log("🎙️ Voiceprint enrollment cancelled.")
                await self._post(enrollment.channel, "Enrollment cancelled.")
                await self._say("Okay, enrollment cancelled.")
            elif text and stop is None:
                await self._say(enrollment.retry_line())
            return
        if not enrollment.done:
            await self._say(enrollment.progress_line())
            return
        self.gate.enrollment = None
        # Not part of the turn, which the next thing you say cancels.
        self._spawn(self._save(enrollment))

    async def _save(self, enrollment: Enrollment) -> None:
        try:
            voiceprint = await self.gate.enroll(enrollment.readings)
        except Exception as e:
            self._log(f"🎙️ Could not make the voiceprint: {type(e).__name__}: {e}")
            await self._post(enrollment.channel, f"⚠️ I couldn't make your voiceprint ({e}). "
                                                 f"Type `!enroll` to try again.")
            await self._say("Sorry, I couldn't make your voiceprint. Type enroll to try again.")
            return
        self._log(f"🎙️ Voiceprint saved: {voiceprint.readings} readings, consistency {voiceprint.consistency:.2f}.")
        await self._post(enrollment.channel, saved_message(voiceprint, self.gate.mode))
        await self._say("Done. Your voiceprint is saved.")

    async def _post(self, channel: Any, text: str) -> None:
        if channel is None:
            return
        try:
            await channel.send(text)
        except Exception as e:
            self._log(f"Could not post in #{getattr(channel, 'name', channel)}: {e}")
