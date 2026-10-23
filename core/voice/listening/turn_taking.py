"""Turn-taking rules for voice: when a turn has ended, and when new speech interrupts.

Pure functions over transcript text, so the rules can be tested without audio.
The live transcriber supplies the text while the user is still talking:

  * `end_of_turn_ms` reads the words heard so far to decide how long a silence
    ends the turn. A finished sentence ends quickly; a trailing "and" or "um"
    waits longer; with no words yet, the old fixed wait applies.
  * `confirms_barge_in` and `is_backchannel` decide what speech over a busy
    agent means: an interruption ("stop", "actually, make it Friday"), or just
    "mm-hmm" while listening, which must not cut the answer off.

A stop phrase interrupts at once, however short. The phrases, and what "stop"
or "wait" on its own then does, are the stop and wait commands
(core/runtime/commands/spoken/stop.py and wait.py).
"""
from typing import Iterable

from core.runtime.commands.spoken.stop import is_stop_request
from core.runtime.commands.spoken.words import words
from core.voice.foundation.settings import (
    BARGE_IN_MIN_WORDS,
    END_OF_TURN_COMPLETE_MS,
    END_OF_TURN_CONTINUING_MS,
    END_OF_TURN_DEFAULT_MS,
)

# A last word that promises more: a conjunction, an article, possessive or
# preposition left hanging, a helping verb, or a filler while the speaker thinks.
CONTINUATION_WORDS = frozenset({
    "and", "but", "or", "so", "because", "cause", "if", "then", "than", "that", "which", "who",
    "the", "a", "an", "my", "your", "our", "their", "his", "her", "its", "this", "these", "those",
    "to", "of", "for", "with", "about", "at", "in", "on", "from", "by", "into", "like",
    "is", "are", "was", "were", "be", "can", "could", "would", "should", "will", "also", "plus",
    "um", "uh", "er", "erm", "hmm",
})

# Said while listening, not to take the turn. Single words, and short phrases
# compared after normalization ("Mm-hmm." -> "mm hmm").
BACKCHANNEL_WORDS = frozenset({
    "ok", "okay", "k", "yeah", "yep", "yes", "yup", "mhm", "mm", "hmm", "right", "sure",
    "cool", "nice", "great", "thanks", "alright", "oh", "ah", "wow", "uh", "huh", "aha",
})
BACKCHANNEL_PHRASES = frozenset({
    "got it", "thank you", "all right", "i see", "uh huh", "mm hmm", "sounds good", "makes sense",
})

# A complete-looking sentence this short ("Hey Daisy.") is often a lead-in, so
# it keeps the default wait rather than the quick one.
_MIN_COMPLETE_WORDS = 3


def is_backchannel(text: str) -> bool:
    """'okay', 'yeah sure', 'mm-hmm': listening noises, not a new request."""
    tokens = words(text)
    if not tokens or len(tokens) > 3:
        return False
    if " ".join(tokens) in BACKCHANNEL_PHRASES:
        return True
    return all(token in BACKCHANNEL_WORDS for token in tokens)


def confirms_barge_in(text: str) -> bool:
    """Whether speech over the agent is a real interruption yet."""
    if is_stop_request(text):
        return True
    return len(words(text)) >= BARGE_IN_MIN_WORDS and not is_backchannel(text)


def end_of_turn_ms(text: str) -> int:
    """How long a silence ends the turn, given the words heard so far."""
    stripped = (text or "").strip()
    tokens = words(stripped)
    if not tokens:
        return END_OF_TURN_DEFAULT_MS
    if tokens[-1] in CONTINUATION_WORDS:
        return END_OF_TURN_CONTINUING_MS
    if stripped[-1] in ".?!" and (len(tokens) >= _MIN_COMPLETE_WORDS or is_stop_request(stripped)):
        return END_OF_TURN_COMPLETE_MS
    return END_OF_TURN_DEFAULT_MS


def merge_transcripts(parts: Iterable[str]) -> str:
    """One prompt from utterances spoken as one request, in order."""
    return " ".join(part.strip() for part in parts if part and part.strip())
