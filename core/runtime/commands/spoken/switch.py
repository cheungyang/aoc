"""Switch: "switch me to Daisy", "let's start with Grace", or just "Grace." --
moves the voice room's focus (which text channel the next turn belongs to) and
moves nobody. The room does the moving (core/voice/room/room_switch.py).

A switch is said one of three ways, told apart by two facts: whether a switch
phrase leads it (only then is a near miss worth a model call), and whether it
has any switch words at all.

  asked   A switch phrase, then the target: "switch me to Daisy", "take me to
          the market scout", "can I talk to Frankie". An exact agent or
          channel name switches at once, with no model call; one that names
          nothing exactly gets one small model call and "Do you mean ...?".
  worded  A name with switch words, but no switch phrase leading it: "let's
          start with Grace", "chat with Marvin", "I'm with Marvin". An exact
          name only. "Start with" belongs here, not with the switch phrases:
          it also begins answers ("Let's start with Michael.", said to Grace
          in the logs), which must never cost a model call or get "Do you
          mean ...?". The recognizer made "I'm with", "chat with" and a lone
          "with" out of it.
  bare    A name with nothing around it but openers or politeness: "Grace.",
          "Grace. Grace.", "hey Daisy", "thanks, Grace". Said by voice, a name
          almost always means "take me there"; before, it failed every time in
          the prod logs ("Who's it for?", "I'm ready to design Frankie"). But
          it is also what a recognizer makes of noise, so the room checks it
          sounds like a voice; the agent you're with gets its own name as a
          request ("Thanks, Grace" is for Grace); and a move out of a
          conversation says how to undo it ("Frankie here. Say 'go back' for
          Grace.").

Matching is strict all ways. After a switch phrase, the whole remainder is the
target, so "go to day planning and add a task" is not a move. Around a name
said without one, every other word must be a switch word, an opener or a
politeness, so "Grace, continue our conversation" (a request, which the router
hands to Grace) and "not Grace" are never switches.
"""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.runtime.commands.base import SpokenCommand, SpokenContext
from core.runtime.commands.spoken.words import alias_key, clean
from core.runtime.observability.turn_trace import Outcome

# A switch phrase at the start of the utterance, after optional politeness.
# The capture is everything that follows: the target being asked for.
_ASKED = re.compile(
    r"^(?:(?:hey|hi|ok|okay|so|um|uh|please|concierge)[\s,]+)*"
    r"(?:can you |could you |would you |will you )?(?:please )?"
    r"(?:take me (?:to|into)|bring me (?:to|into)|move me (?:to|into)|switch (?:me )?(?:to|over to)|"
    r"connect me (?:to|with)|go (?:to|into)|let'?s go (?:to|into)|"
    r"(?:i want|i'?d like) to (?:talk|speak) (?:to|with)|(?:can|could|may) i (?:please )?(?:talk|speak) (?:to|with)|"
    r"let me (?:talk|speak) (?:to|with)|(?:talk|speak) (?:to|with)|"
    r"(?:go |take me |bring me |head )?back (?:to|into)|return to)\s+"
    r"(?P<target>.+)$"
)

# A name said without a switch phrase: at most this many words in all, the
# name included ("hey Grace, are you there")...
MAX_WORDS = 6
# ...of which the name itself at most this many ("receipt processor", "day planning").
MAX_NAME_WORDS = 3

# Openers, politeness and a presence check ("Grace, are you there?"): a name
# with only these around it is bare. Trailing ones are trimmed off what a
# switch phrase asks for too ("switch to Grace again").
FILLER = frozenset({
    "hey", "hi", "hello", "yo", "ok", "okay", "oh", "so", "um", "uh", "hmm", "well", "alright",
    "please", "thanks", "thank", "you", "now", "again", "just", "there", "are", "the",
})
# Switch words: they say you want to talk to someone ("let's start with",
# "I'm with", "can I talk to"), so a name said with them isn't bare.
SWITCH_WORDS = frozenset({
    "let's", "lets", "let", "me", "i", "i'm", "im", "i'd", "can", "could", "would", "will", "we",
    "want", "wanna", "like", "need", "to", "with", "start", "from", "talk", "speak", "chat", "go",
    "back", "over", "switch", "get", "bring", "take", "ask", "check", "in", "put", "connect",
})
# Any of these and a name said without a switch phrase is no switch: "not
# Grace", "don't get Grace".
NEGATIONS = frozenset({"not", "no", "don't", "dont", "never", "without", "nor", "isn't", "stop"})


@dataclass(frozen=True)
class SwitchRequest:
    """What a switch asks for, and how it was said."""
    phrase: str   # the target, in `alias_key` form, as the room's alias table is
    asked: bool   # a switch phrase led it: a target named nowhere exactly is worth a model call
    bare: bool    # no switch words at all: a name, with at most openers or politeness around it


def parse_switch(transcript: str) -> Optional[SwitchRequest]:
    """The switch `transcript` asks for: a switch phrase and its target, or
    failing that, a name with only switch words, openers or politeness
    around it. None if neither. Whether the target names anyone is the
    room's to say."""
    text = clean(transcript)
    found = _ASKED.match(text)
    if found:
        target = found.group("target").split()
        while target and target[-1] in FILLER:
            target.pop()
        phrase = alias_key(" ".join(target))
        return SwitchRequest(phrase, asked=True, bare=False) if phrase else None
    words = text.split()
    if not words or len(words) > MAX_WORDS or any(word in NEGATIONS for word in words):
        return None
    name = _collapse_repeats([word for word in words if word not in FILLER and word not in SWITCH_WORDS])
    if not name or len(name) > MAX_NAME_WORDS:
        return None
    phrase = alias_key(" ".join(name))
    if not phrase:
        return None
    return SwitchRequest(phrase, asked=False, bare=not any(word in SWITCH_WORDS for word in words))


def _collapse_repeats(words: List[str]) -> List[str]:
    """A name said more than once, once: ["grace", "grace"] -> ["grace"]."""
    for size in range(1, len(words) // 2 + 1):
        if len(words) % size == 0 and words == words[:size] * (len(words) // size):
            return words[:size]
    return words


def match_target(transcript: str, aliases: Dict[str, str]) -> Optional[str]:
    """The focus key `transcript` asks to switch to by an exact name, or None."""
    request = parse_switch(transcript)
    return aliases.get(request.phrase) if request is not None else None


class SwitchCommand(SpokenCommand):
    name = "switch"
    summary = ("Moves the voice room to an agent or channel, asked for or just named; a name it "
               "doesn't know, after a switch phrase, gets \u201cDo you mean ...?\u201d.")
    examples = ("switch me to Daisy", "take me to the market scout", "let's start with Grace", "Grace.")
    outcome = Outcome.FOCUS_SWITCH

    def match(self, transcript: str) -> Optional[str]:
        request = parse_switch(transcript)
        return request.phrase if request is not None else None

    async def run(self, ctx: SpokenContext, phrase: str) -> bool:
        request = parse_switch(ctx.heard)
        if request is None or request.phrase != phrase:
            # Without the words heard, `phrase` is taken as asked for: what
            # "switch to <phrase>" has always meant.
            request = SwitchRequest(phrase, asked=True, bare=False)
        outcome = await ctx.voice.switch_to_named(phrase, ctx.say, asked=request.asked, bare=request.bare,
                                                  turn=ctx.turn)
        if outcome is None:
            return False  # it names no one, or a bare name is the agent you're with: a request
        if outcome == Outcome.NO_TRANSCRIPT and ctx.turn is not None:
            # Noise, not words: this utterance goes, and anything said before
            # it stays unanswered, as for any utterance with no words.
            ctx.turn.hold()
        ctx.outcome = outcome
        return True
