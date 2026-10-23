"""Stop: "stop", "never mind", "cancel". Said on its own, it stops the reply and
drops the request it cut off; "wait" (wait.py) keeps it. Either way it is not
a request for the agent, and speech that goes on past the phrase ("wait, make
it Friday") is one.

These phrases are also the ones that interrupt the agent at once, however
short (core/voice/listening/turn_taking.py's barge-in and end of turn).
"""
from typing import Iterable, List, Optional

from core.runtime.commands.base import SpokenCommand, SpokenContext
from core.runtime.commands.spoken.words import words
from core.runtime.observability.turn_trace import Outcome

# Phrases that interrupt at once, however short.
STOP_PHRASES = (
    "stop", "wait", "hold on", "hang on", "hold up", "cancel", "never mind", "nevermind",
    "forget it", "pause", "quiet", "shut up", "enough", "no no",
)
# Stop phrases that mean "let me add something" rather than "drop it".
HOLD_PHRASES = ("wait", "hold on", "hang on", "hold up")
# Words that can surround a stop phrase without making it a request of its own
# ("okay, stop", "wait a second", "stop talking please").
_STOP_FILLER = frozenset({
    "okay", "ok", "please", "just", "sorry", "hey", "oh", "um", "uh", "talking", "now", "right",
    "a", "sec", "second", "moment", "minute", "it", "that",
})
_STOP_WORDS = frozenset(word for phrase in STOP_PHRASES for word in phrase.split()) | _STOP_FILLER


def _contains(text_words: List[str], phrases: Iterable[str]) -> bool:
    joined = " " + " ".join(text_words) + " "
    return any(f" {phrase} " in joined for phrase in phrases)


def is_stop_request(text: str) -> bool:
    """Whether the speech asks the agent to stop, within its first few words."""
    return _contains(words(text)[:4], STOP_PHRASES)


def bare_stop(text: str) -> Optional[str]:
    """For speech that is only a stop phrase, what it asks for; None otherwise.

    "hold": stop talking, but keep the unanswered words ("wait", "hold on a
    sec"): the user is about to add to them. "cancel": stop, and drop them
    ("stop", "never mind"). Either way it is not a request for the agent.
    Speech that goes on past the phrase ("wait, make it Friday") is a request.
    """
    tokens = words(text)
    if not tokens or not is_stop_request(text) or any(token not in _STOP_WORDS for token in tokens):
        return None
    return "hold" if _contains(tokens, HOLD_PHRASES) else "cancel"


class StopCommand(SpokenCommand):
    name = "stop"
    summary = "Stops the reply, and drops the request it cut off."
    examples = ("stop", "never mind", "cancel")
    outcome = Outcome.STOP_CANCEL

    def match(self, transcript: str) -> Optional[str]:
        return "" if bare_stop(transcript) == "cancel" else None

    async def run(self, ctx: SpokenContext, args: str) -> bool:
        ctx.log(f"✋ Heard {ctx.heard!r}: stopping, request dropped.")
        return True
