"""Switching by voice: "switch me to Daisy" moves the focus to Daisy's channel.
Its phrases are the switch and where-am-I commands'
(core/runtime/commands/spoken/); this is the moving.

There is one voice room; a switch changes which text channel the next turn
belongs to (focus.py) and moves nobody. An exact agent or channel name switches
at once, with no model call.

A switch phrase that names nothing exactly ("take me to exercise") gets one
small model call to find the closest agent (near_miss.py), and an answer that
tells you the words to use: "Do you mean fitness coach Frankie? Say 'switch to
Frankie' to get there." Nothing waits for a yes or a no, so there is no mode
to be in. If nothing is close, the phrase is an ordinary request for the
agent you're with ("take me to the airport on Friday"). A request that only
starts like a switch ("go to Daisy and add a task", "talk to me about
dinner") is one without asking.

`FocusSwitcher` does the moving: asking, checking the target can be reached,
moving the focus, greeting in the target's voice. Features built on top switch
through `switch_to` too: the inbox's "next agent" (core/voice/briefing/hook.py)
arrives the same way.
"""
from typing import Awaitable, Callable, Dict, Optional, Protocol

from core.voice.room import near_miss
from core.voice.room.aliases import build_aliases
from core.voice.room.focus import FocusDirectory, VoiceFocus


# A switch target is a name or a few words about one ("exercise", "my workout
# coach"). Anything longer is a request that happens to start like a switch,
# and gets no model call.
MAX_TARGET_WORDS = 6
# After a name, these start a request: "go to Daisy and add a task" stays an
# ordinary request, so a switch is always its own clear step.
_JOINERS = {"and", "then", "to", "so", "for", "about", "because", "with"}


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
        self._resolve = resolver if resolver is not None else near_miss.closest_target

    def _log(self, message: str) -> None:
        print(f"[VoiceManager:{self.agent_id}] {message}")

    async def switch_to_named(self, phrase: str, say: Say) -> bool:
        """Answers a switch request for `phrase`, what was named after "switch me
        to" (core/runtime/commands/spoken/switch.py). True if it was one: the
        focus moved, or you were told the words for a near miss. False: the
        words are an ordinary request for the agent you're with.

        `say(text, agent_id)` speaks a line in `agent_id`'s voice. Arriving is
        announced in the target's own voice; questions about switching are
        asked by the concierge, whose job routing is.
        """
        directory = self.voice.directory()
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
        self._log(f"🤔 {phrase!r} named no one exactly; suggesting #{target.channel_name}.")
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
