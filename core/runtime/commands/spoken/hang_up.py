"""Hang up: "hanging up", "I'm hanging up now" -- ends the voice session the way
a phone call ends. The agent you're with says goodbye, you're disconnected from
the voice channel, and the bot leaves (VoiceManager `hang_up`,
core/voice/voice_manager.py). Joining the channel again starts a new session,
as it always does.

Only the phrase on its own is the command, with the words people wrap it in
("okay, bye, hanging up", "you can hang up now"). Speech that goes on to
something else ("remind me to hang up the laundry", "don't hang up") is a
request for the agent. "Hang on" is a hold (stop.py and wait.py), not this.
"""
from typing import List, Optional

from core.runtime.commands.base import SpokenCommand, SpokenContext
from core.runtime.commands.spoken.words import clean
from core.runtime.observability.turn_trace import Outcome

# The phrase itself, as words.
_HANG_UP = (("hang", "up"), ("hanging", "up"))
# Words that can come before it without making the speech a request of its own:
# openers, goodbyes, and who's hanging up ("I'm going to", "you can", "let's").
_BEFORE = frozenset({
    "ok", "okay", "alright", "all", "right", "so", "well", "um", "uh", "oh", "hey", "and", "then", "concierge",
    "bye", "goodbye", "good", "thanks", "thank", "you", "please", "just", "now",
    "i", "i'm", "im", "am", "i'll", "will", "we", "we're", "we'll", "let's", "lets", "let", "me",
    "can", "could", "gonna", "going", "to", "go", "ahead", "want", "need", "have", "got", "gotta", "about",
})
# Words that can come after it: what's hung up, and the goodbyes.
_AFTER = frozenset({
    "now", "the", "call", "phone", "on", "you", "bye", "goodbye", "good", "thanks", "thank",
    "talk", "to", "soon", "later", "see", "ya", "then", "for", "and",
})
# Longer than this, it's a sentence about something, not a goodbye.
_MAX_WORDS = 12


def _tokens(text: str) -> List[str]:
    """The utterance as words, apostrophes kept ("I’m" -> "i'm")."""
    return clean(text).split()


def is_hang_up(text: str) -> bool:
    """Whether the speech is only a hang-up, in the words around it that change nothing."""
    tokens = _tokens(text)
    if not tokens or len(tokens) > _MAX_WORDS:
        return False
    for start in range(len(tokens) - 1):
        if tuple(tokens[start:start + 2]) in _HANG_UP:
            before, after = tokens[:start], tokens[start + 2:]
            return all(word in _BEFORE for word in before) and all(word in _AFTER for word in after)
    return False


class HangUpCommand(SpokenCommand):
    name = "hang up"
    summary = "Ends the voice session like a call: a goodbye, then you're disconnected and the bot leaves."
    examples = ("hanging up", "I'm hanging up now")
    outcome = Outcome.HANG_UP

    def match(self, transcript: str) -> Optional[str]:
        return "" if is_hang_up(transcript) else None

    async def run(self, ctx: SpokenContext, args: str) -> bool:
        ctx.log(f"📴 Heard {ctx.heard!r}: hanging up.")
        speaker = ctx.turn.speaker() if ctx.turn is not None else None
        return await ctx.voice.hang_up(ctx.say, speaker)
