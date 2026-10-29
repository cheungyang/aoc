"""Switching by voice: "switch me to Daisy", or just "Daisy.", moves the focus
to Daisy's channel. Its phrases are the switch and where-am-I commands'
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

A name said without a switch phrase -- "let's start with Grace", or bare,
"Grace." (core/runtime/commands/spoken/switch.py) -- switches too, but only to
an exact name: it is already a guess about what you meant, with no near miss
on top. A recognizer also makes bare names out of noise, so a bare name must
sound like a voice; and the agent you're with gets its own bare name as a
request ("Thanks, Grace"), where switch words get "You're already with Grace."

Any move that takes you out of a conversation says how to undo it: "Frankie
here. Say 'go back' for Grace." That is a switch however it was said, and an
agent handing you to a teammate (`switch_to_agent`).

`FocusSwitcher` does the moving: asking, checking the target can be reached,
moving the focus, greeting in the target's voice. Features built on top switch
through `switch_to` too: the inbox's "next agent" (core/voice/briefing/hook.py)
arrives the same way, and so does "go back".
"""
from typing import Awaitable, Callable, Dict, Optional, Protocol

from core.runtime.commands.spoken.words import alias_key
from core.runtime.observability.turn_trace import Outcome
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
    Not a request that starts like a switch: "talk to me about dinner", "can I
    talk to you about my run", "go to Daisy and add a task", or anything too
    long to be a name."""
    words = phrase.split()
    if not words or len(words) > MAX_TARGET_WORDS or words[0] in ("me", "us", "you"):
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

    async def switch_to_named(self, phrase: str, say: Say, *, asked: bool = True, bare: bool = False,
                              noise: Callable[[], Optional[str]] = lambda: None,
                              live: Callable[[VoiceFocus], bool] = lambda focus: False) -> Optional[str]:
        """Answers a switch to `phrase` (core/runtime/commands/spoken/switch.py).
        Returns the turn's outcome when it answered: the focus moved, you were
        told you're there already or the words for a near miss, or a bare name
        was dropped as noise. None: the words are an ordinary request for the
        agent you're with.

        Only what a switch phrase `asked` for ("switch me to Daisy") gets a
        near miss for a name that isn't exact; said without one ("let's start
        with Daisy", "Daisy."), the switch is a guess, and takes only an exact
        name. A `bare` name, with no switch words at all, is also what a
        recognizer makes of noise: `noise()` says why its utterance doesn't
        sound like a voice, if it doesn't. And the agent you're with gets its
        own bare name as a request ("Thanks, Grace"); asked for in switch
        words, you're told you're with it already.

        `live(focus)` says whether you were talking in `focus` a moment ago:
        a move out of it says how to come back.

        `say(text, agent_id)` speaks a line in `agent_id`'s voice. Arriving is
        announced in the target's own voice; questions about switching are
        asked by the concierge, whose job routing is.
        """
        directory = self.voice.directory()
        aliases = build_aliases(directory.alias_sources())
        target = directory.get(aliases.get(phrase))
        here = self.voice.focus()
        if target is not None:
            if bare:
                why = noise()
                if why:
                    self._log(f"🦜 {phrase!r} on its own doesn't sound like a voice ({why}); dropped as noise.")
                    return Outcome.NO_TRANSCRIPT
                if target.key == here.key:
                    return None  # "Thanks, Grace", said to Grace, is for Grace
            leaving = target.key != here.key and live(here)
            await self.switch_to(target, directory, say, back_to=here if leaving else None)
            return Outcome.FOCUS_SWITCH
        if not asked or not could_name_a_target(phrase, aliases):
            return None  # a guess that names no one; or a request that starts like a switch

        # A near miss: ask the model which agent is closest, then tell you the
        # words that reach it. Nothing waits for an answer.
        key = await self._resolve(phrase, directory)
        target = directory.get(key) if key else None
        if target is None:
            return None  # nothing close: an ordinary request for the agent you're with
        if target.key == here.key:
            await say(directory.already(target), target.agent_id)
            return Outcome.FOCUS_SWITCH
        self._log(f"🤔 {phrase!r} named no one exactly; suggesting #{target.channel_name}.")
        await say(f"Do you mean {directory.role(target)}? Say '{directory.words_to_switch(target)}' to get there.",
                  directory.voice_host)
        return Outcome.FOCUS_SWITCH

    async def switch_to_agent(self, agent: str, say: Say,
                              live: Callable[[VoiceFocus], bool] = lambda focus: False) -> bool:
        """An agent's reply handed you to `agent`, a teammate's id or name
        (<switch to="goal-setter"/>, core/channel/response_parser.py): moves the
        focus to the teammate's home channel and greets you there, as "switch
        to ..." would, saying how to come back if it leaves a conversation
        (`live`). False: no teammate by that id or name has a channel to go to,
        or it's the one you're with."""
        directory = self.voice.directory()
        key = directory.homes.get(agent) or build_aliases(directory.alias_sources()).get(alias_key(agent))
        target = directory.get(key) if key else None
        if target is None:
            self._log(f"A reply handed you to {agent!r}, who has no channel to go to.")
            return False
        here = self.voice.focus()
        if target.key == here.key:
            return False  # an agent naming itself: there's nowhere to move
        return await self.switch_to(target, directory, say, back_to=here if live(here) else None)

    async def switch_to(self, target: VoiceFocus, directory: FocusDirectory, say: Say,
                        back_to: Optional[VoiceFocus] = None) -> bool:
        """Moves the focus to `target` and greets you there, in its voice; then
        whatever the voice room adds on arriving. True: the request is answered.
        With `back_to`, the move took you out of a conversation there, and the
        greeting says how to undo it."""
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
        greeting = directory.greeting(target)
        if back_to is not None:
            greeting = f"{greeting} {directory.go_back_hint(back_to)}"
        await say(greeting, target.agent_id)
        await self.voice.arrive(target, say)
        return True
