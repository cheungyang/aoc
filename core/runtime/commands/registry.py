"""CommandRegistry: commands by name, tried in order. And the three registries,
one for each way a command is given."""
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from core.runtime.commands.base import Command, CommandContext
from core.runtime.commands.control.claim import ClaimCommand
from core.runtime.commands.control.release import ReleaseCommand
from core.runtime.commands.control.status import StatusCommand
from core.runtime.commands.spoken.next_agent import NextAgentCommand
from core.runtime.commands.spoken.stop import StopCommand
from core.runtime.commands.spoken.switch import SwitchCommand
from core.runtime.commands.spoken.wait import WaitCommand
from core.runtime.commands.spoken.where_am_i import WhereAmICommand
from core.runtime.commands.typed.compact import CompactCommand
from core.runtime.commands.typed.enroll import EnrollCommand
from core.runtime.commands.typed.gate import GateCommand
from core.runtime.commands.typed.help import HelpCommand
from core.runtime.commands.typed.inbox import InboxCommand
from core.runtime.commands.typed.join import JoinCommand
from core.runtime.commands.typed.leave import LeaveCommand
from core.runtime.commands.typed.new import NewCommand
from core.runtime.commands.typed.new_all import NewAllCommand
from core.runtime.commands.typed.restart import RestartCommand
from core.runtime.commands.typed.voiceprint import VoiceprintCommand


class CommandRegistry:
    """Commands in the order they're tried. A name or alias is taken once."""

    def __init__(self, commands: Iterable[Command] = ()):
        self._commands: List[Command] = []
        self._by_name: Dict[str, Command] = {}
        for command in commands:
            self.register(command)

    def register(self, command: Command) -> None:
        names = (command.name, *command.aliases)
        taken = [name for name in names if name in self._by_name]
        if taken:
            raise ValueError(f"Command name already taken: {', '.join(taken)}")
        self._commands.append(command)
        for name in names:
            self._by_name[name] = command

    def find(self, name: str) -> Optional[Command]:
        return self._by_name.get(name)

    def __iter__(self) -> Iterator[Command]:
        return iter(self._commands)

    def match(self, text: str) -> Optional[Tuple[Command, str]]:
        """The first command `text` gives, with its arguments."""
        for command in self._commands:
            args = command.match(text)
            if args is not None:
                return command, args
        return None

    async def answer(self, text: str, ctx: CommandContext) -> Optional[Command]:
        """Runs the first command that `text` gives, is answered here, and
        answers it. None if there's none: the text is a message for the agent."""
        for command in self._commands:
            args = command.match(text)
            if args is None or not command.available(ctx):
                continue
            if await command.run(ctx, args) is not False:
                return command
        return None


# `!name`, typed to the Discord bot (core/channel/discord/runner.py).
TYPED = CommandRegistry([
    NewCommand(), NewAllCommand(), CompactCommand(), RestartCommand(),
    JoinCommand(), LeaveCommand(), InboxCommand(), EnrollCommand(), VoiceprintCommand(), GateCommand(),
])

# Said in the voice room (core/voice/turn.py), tried in this order: a stop
# first; "next agent" and "where am I" before the switch phrases, which would
# otherwise take them for a near miss.
SPOKEN = CommandRegistry([StopCommand(), WaitCommand(), NextAgentCommand(), WhereAmICommand(), SwitchCommand()])

# Typed in the ownership control thread (core/channel/discord/ownership.py).
CONTROL = CommandRegistry([ClaimCommand(), ReleaseCommand(), StatusCommand()])

# `!help` lists every command, from all three.
TYPED.register(HelpCommand(TYPED, SPOKEN, CONTROL))
