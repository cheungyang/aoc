"""CommandRegistry: commands by name, tried in order. And the three registries,
one for each way a command is given."""
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from core.learning import events
from core.runtime.commands.base import Command, CommandContext
from core.runtime.commands.control.claim import ClaimCommand
from core.runtime.commands.control.release import ReleaseCommand
from core.runtime.commands.control.status import StatusCommand
from core.runtime.commands.spoken.go_back import GoBackCommand
from core.runtime.commands.spoken.hang_up import HangUpCommand
from core.runtime.commands.spoken.next_agent import NextAgentCommand
from core.runtime.commands.spoken.presence import PresenceCommand
from core.runtime.commands.spoken.stop import StopCommand
from core.runtime.commands.spoken.switch import SwitchCommand
from core.runtime.commands.spoken.wait import WaitCommand
from core.runtime.commands.spoken.where_am_i import WhereAmICommand
from core.runtime.commands.typed.compact import CompactCommand
from core.runtime.commands.typed.enroll import EnrollCommand
from core.runtime.commands.typed.followups import FollowUpsCommand
from core.runtime.commands.typed.gate import GateCommand
from core.runtime.commands.typed.help import HelpCommand
from core.runtime.commands.typed.inbox import InboxCommand
from core.runtime.commands.typed.job import JobCommand
from core.runtime.commands.typed.jobs import JobsCommand
from core.runtime.commands.typed.join import JoinCommand
from core.runtime.commands.typed.kill import KillCommand
from core.runtime.commands.typed.learn import LearnCommand
from core.runtime.commands.typed.leave import LeaveCommand
from core.runtime.commands.typed.new import NewCommand
from core.runtime.commands.typed.new_all import NewAllCommand
from core.runtime.commands.typed.python import PythonCommand
from core.runtime.commands.typed.restart import RestartCommand
from core.runtime.commands.typed.signals import SignalsCommand
from core.runtime.commands.typed.voiceprint import VoiceprintCommand


class CommandRegistry:
    """Commands in the order they're tried. A name or alias is taken once. `kind` (typed,
    spoken or control) is how its commands are given, as the learning loop's command log
    records them (core/learning/events.py)."""

    def __init__(self, commands: Iterable[Command] = (), kind: str = ""):
        self.kind = kind
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
        answers it. None if there's none: the text is a message for the agent.
        The one that answers is logged for the learning loop.

        One the person may not give (`Command.refusal`) gets a line saying so
        instead, from one bot (`says_refusal`), and counts as answered: it
        never reaches the agent either, and isn't logged."""
        for command in self._commands:
            args = command.match(text)
            if args is None or not command.available(ctx):
                continue
            refusal = command.refusal(ctx)
            if refusal is not None:
                if command.says_refusal(ctx):
                    print(f"[commands] Refused {ctx.person}: {refusal}")
                    await ctx.reply(refusal)
                return command
            # Read before it runs: "go back" or a switch moves the voice room.
            origin = events.command_origin(ctx) if events.observing() else None
            if await command.run(ctx, args) is not False:
                events.record_command(self.kind, command.name, ctx, args, origin=origin)
                return command
        return None


# `!name`, typed to the Discord bot (core/channel/discord/runner.py).
TYPED = CommandRegistry([
    NewCommand(), NewAllCommand(), CompactCommand(), RestartCommand(), JobsCommand(), JobCommand(), KillCommand(),
    PythonCommand(), SignalsCommand(), FollowUpsCommand(), LearnCommand(), JoinCommand(), LeaveCommand(),
    InboxCommand(), EnrollCommand(), VoiceprintCommand(), GateCommand(),
], kind="typed")

# Said in the voice room (core/channel/voice/turn.py), tried in this order: a stop
# first, then a hang-up; then "next agent", "where am I" and "go back" before
# the switch, which would otherwise take them for a name ("back", "next agent").
# "Hello?" names no one, so its place before the switch is only for reading.
SPOKEN = CommandRegistry([StopCommand(), WaitCommand(), HangUpCommand(), NextAgentCommand(), WhereAmICommand(),
                          GoBackCommand(), PresenceCommand(), SwitchCommand()], kind="spoken")

# Typed in the ownership control thread (core/channel/discord/ownership.py).
CONTROL = CommandRegistry([ClaimCommand(), ReleaseCommand(), StatusCommand()], kind="control")

# `!help` lists every command, from all three.
TYPED.register(HelpCommand(TYPED, SPOKEN, CONTROL))
