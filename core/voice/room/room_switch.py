"""Switching by voice: "switch me to Daisy" moves the focus to Daisy's channel.

There is one voice room; a switch changes which text channel the next turn
belongs to (focus.py) and moves nobody. Matched on the transcript before the
turn is routed. An exact agent or channel name switches at once, with no model
call. Matching is deliberately strict -- the whole remainder after the command
must be a known name -- so "go to day planning and add a task" is not a move.

A switch phrase that names nothing exactly ("take me to exercise") gets one
small model call to find the closest agent (near_miss.py), and an answer that
tells you the words to use: "Do you mean fitness coach Frankie? Say 'switch to
Frankie' to get there." Nothing waits for a yes or a no, so there is no mode
to be in. If nothing is close, the phrase is an ordinary request for the
agent you're with ("take me to the airport on Friday"). A request that only
starts like a switch ("go to Daisy and add a task", "talk to me about
dinner") is one without asking.

The matching is pure functions; `FocusSwitcher` does the rest (asking,
checking the target can be reached, moving the focus, greeting in the target's
voice). Features built on top switch through `switch_to` too: the inbox's
"next agent" (core/inbox/voice_briefing.py) arrives the same way.
"""
import re
from typing import Awaitable, Callable, Dict, Iterable, Optional, Protocol, Tuple

from core.voice.room.focus import FocusDirectory, VoiceFocus

# A command at the start of the utterance, after optional politeness. The
# capture is everything that follows: the target being asked for.
_COMMAND = re.compile(
    r"^(?:(?:hey|hi|ok|okay|so|um|uh|please|concierge)[\s,]+)*"
    r"(?:can you |could you |would you |will you )?(?:please )?"
    r"(?:take me (?:to|into)|bring me (?:to|into)|move me (?:to|into)|switch (?:me )?(?:to|over to)|"
    r"go (?:to|into)|let'?s go (?:to|into)|i want to (?:talk|speak) (?:to|with)|"
    r"let me (?:talk|speak) (?:to|with)|(?:talk|speak) (?:to|with)|"
    r"(?:go |take me |bring me |head )?back (?:to|into)|return to|start (?:with|from))\s+"
    r"(?P<target>.+)$"
)

# Words around a name that don't change which target is meant.
_FILLER = re.compile(r"\b(?:the|room|channel|voice|chat|please|now|thanks|thank you)\b")

# "Where am I?": answered from the focus, without a model call. "Who are you?"
# is not here on purpose: the agent you're with answers it, in its own words,
# as it would in its channel.
_WHERE = re.compile(
    r"^(?:(?:hey|hi|ok|okay|so|um|uh|concierge)[\s,]+)*"
    r"(?:where am i|where are we|which (?:room|channel) am i in|what (?:room|channel) is this)(?: right now| now)?$"
)

# A switch target is a name or a few words about one ("exercise", "my workout
# coach"). Anything longer is a request that happens to start like a switch,
# and gets no model call.
MAX_TARGET_WORDS = 6
# After a name, these start a request: "go to Daisy and add a task" stays an
# ordinary request, so a switch is always its own clear step.
_JOINERS = {"and", "then", "to", "so", "for", "about", "because", "with"}


def _clean(text: str) -> str:
    """Lowercase, punctuation removed except apostrophes, single spaces."""
    text = (text or "").lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9'\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize(text: str) -> str:
    """The comparable form of a phrase or alias: possessives and apostrophes dropped too."""
    text = re.sub(r"'s\b", "", _clean(text)).replace("'", "")
    return re.sub(r"\s+", " ", text).strip()


def _key(text: str) -> str:
    """A phrase or alias with the filler words gone: what the two are compared as."""
    return re.sub(r"\s+", " ", _FILLER.sub(" ", normalize(text))).strip()


def parse_room_request(transcript: str) -> Optional[str]:
    """The normalized target phrase if `transcript` asks to switch, else None."""
    match = _COMMAND.match(_clean(transcript))
    if not match:
        return None
    return _key(match.group("target")) or None


def is_where_am_i(transcript: str) -> bool:
    """Whether `transcript` asks where the focus is."""
    return bool(_WHERE.match(_clean(transcript)))


def build_aliases(sources: Iterable[Tuple[str, Iterable[str]]]) -> Dict[str, str]:
    """Alias -> focus key.

    `sources` pairs each focus key with the names that mean it (see
    `FocusDirectory.alias_sources`): a channel's own name, the agent whose home
    it is ("Daisy"), "concierge" for the lobby. An alias claimed by two targets
    is dropped rather than guessed.
    """
    aliases: Dict[str, str] = {}
    ambiguous = set()
    for key, names in sources:
        for name in names:
            alias = _key(name.replace("-", " ")) if isinstance(name, str) else ""
            if not alias:
                continue
            if alias in aliases and aliases[alias] != key:
                ambiguous.add(alias)
            aliases[alias] = key
    for alias in ambiguous:
        del aliases[alias]
    return aliases


def match_target(transcript: str, aliases: Dict[str, str]) -> Optional[str]:
    """The focus key `transcript` asks to switch to by an exact name, or None."""
    target = parse_room_request(transcript)
    if target is None:
        return None
    return aliases.get(target)


def could_name_a_target(phrase: str, aliases: Dict[str, str]) -> bool:
    """Whether a switch phrase that names no one exactly could still mean
    someone ("exercise", "frankly"), and so is worth asking the model about.
    Not a request that starts like a switch: "talk to me about dinner", "go to
    Daisy and add a task", or anything too long to be a name."""
    words = phrase.split()
    if not words or len(words) > MAX_TARGET_WORDS or words[0] in ("me", "us"):
        return False
    return not any(" ".join(words[:i]) in aliases and words[i] in _JOINERS for i in range(1, len(words)))


Say = Callable[..., Awaitable[None]]
Resolver = Callable[[str, FocusDirectory], Awaitable[Optional[str]]]


class Focuser(Protocol):
    """What the switcher needs from whoever owns the focus (VoiceManager)."""

    def directory(self) -> FocusDirectory:
        """Every focus there is, as configured right now."""

    def focus(self) -> VoiceFocus:
        """Where the next turn goes."""

    def set_focus(self, focus: VoiceFocus, *, interrupt: bool = True) -> None:
        """Moves the focus; nothing said to the old one carries over."""

    def focus_channel(self, focus: VoiceFocus):
        """The Discord channel `focus`'s turns are posted in, or None."""

    async def arrive(self, focus: VoiceFocus, say: "Say") -> None:
        """After the greeting: whatever the voice room's hooks add there."""


class FocusSwitcher:
    """Moves the focus to the agent or channel you ask for, and greets you there."""

    def __init__(self, voice: Focuser, agent_id: str, resolver: Optional[Resolver] = None):
        self.voice = voice
        self.agent_id = agent_id
        if resolver is None:
            from core.voice.room.near_miss import closest_target as resolver
        self._resolve = resolver

    def _log(self, message: str) -> None:
        print(f"[VoiceManager:{self.agent_id}] {message}")

    async def handle(self, transcript: str, say: Say) -> bool:
        """Answers a switch request or a where-am-I question. True if
        `transcript` was one of them.

        `say(text, agent_id)` speaks a line in `agent_id`'s voice. Arriving is
        announced in the target's own voice; questions about switching are
        asked by the concierge, whose job routing is.
        """
        directory = self.voice.directory()
        if is_where_am_i(transcript):
            focus = self.voice.focus()
            await say(directory.whereabouts(focus), focus.agent_id)
            return True

        phrase = parse_room_request(transcript)
        if phrase is None:
            return False
        aliases = build_aliases(directory.alias_sources())
        target = directory.get(aliases.get(phrase))
        if target is not None:
            return await self.switch_to(target, directory, say)
        if not could_name_a_target(phrase, aliases):
            return False  # a request that starts like a switch

        # A near miss: ask the model which agent is closest, then tell you the
        # words that reach it. Nothing waits for an answer.
        key = await self._resolve(phrase, directory)
        target = directory.get(key) if key else None
        if target is None:
            return False  # nothing close: an ordinary request for the agent you're with
        if target.key == self.voice.focus().key:
            await say(directory.already(target), target.agent_id)
            return True
        self._log(f"🤔 {transcript!r} named no one exactly; suggesting #{target.channel_name}.")
        await say(f"Do you mean {directory.role(target)}? Say '{directory.words_to_switch(target)}' to get there.",
                  directory.voice_host)
        return True

    async def switch_to(self, target: VoiceFocus, directory: FocusDirectory, say: Say) -> bool:
        """Moves the focus to `target` and greets you there, in its voice; then
        whatever the voice room adds on arriving. True: the request is answered."""
        if target.key == self.voice.focus().key:
            await say(directory.already(target), target.agent_id)
            # Still, whatever it left you: an announcement about the agent
            # you're with says to switch to it, and that must play it.
            await self.voice.arrive(target, say)
            return True
        if self.voice.focus_channel(target) is None:
            self._log(f"Focus switch: #{target.channel_name} can't be found in Discord.")
            await say(f"I can't reach {target.label} right now.", directory.voice_host)
            return True

        self._log(f"🎧 Focus switch: now with {directory.who(target)} in #{target.channel_name}.")
        # The turn asking for the switch is the current one, and it already
        # superseded whatever was in flight; it must not cancel itself.
        self.voice.set_focus(target, interrupt=False)
        await say(directory.greeting(target), target.agent_id)
        await self.voice.arrive(target, say)
        return True
