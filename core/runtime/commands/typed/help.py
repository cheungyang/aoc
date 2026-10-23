"""`!help [command]`: every command there is, typed or spoken, or how to use one."""
from typing import Iterable, List, Optional

from core.runtime.commands.base import (
    CHANNEL,
    VOICE_ROOM,
    ChannelCommand,
    Command,
    SpokenCommand,
    TypedCommand,
    TypedContext,
)

# The typed commands' sections, in the order `!help` lists them.
SECTIONS = ((CHANNEL, "Typed in a channel the bot answers"), (VOICE_ROOM, "Typed, for the voice room"))


def describe(command: Command) -> str:
    """One line: how the command is given, then what it does."""
    if isinstance(command, TypedCommand):
        how = f"`!{command.name}{' ' + command.usage if command.usage else ''}`"
        if command.aliases:
            how += " (or " + ", ".join(f"`!{alias}`" for alias in command.aliases) + ")"
    elif isinstance(command, SpokenCommand):
        how = ", ".join(f"\u201c{example}\u201d" for example in command.examples)
    else:
        how = f"`{getattr(command, 'usage', '') or command.name}`"
    return f"{how}: {command.summary}"


class HelpCommand(ChannelCommand):
    name = "help"
    summary = "Lists every command, typed or spoken; `!help <command>` shows one."
    usage = "[command]"

    def __init__(self, typed: Iterable[Command], spoken: Iterable[Command], control: Iterable[Command]):
        self.typed, self.spoken, self.control = typed, spoken, control

    async def run(self, ctx: TypedContext, args: str) -> None:
        await ctx.reply(self.text(args))

    def text(self, args: str = "") -> str:
        """Every command, by how it's given; or, given a name, that one command."""
        name = (args or "").strip()
        if name:
            command = self.find(name)
            return describe(command) if command else f"There's no command called `{name}`. `!help` lists them all."
        lines: List[str] = []
        for section, heading in SECTIONS:
            lines.append(f"**{heading}**")
            lines += [describe(c) for c in self.typed if getattr(c, "section", None) == section]
        lines.append("**Said in the voice room**")
        lines += [describe(c) for c in self.spoken]
        lines.append("**Typed in the control thread**")
        lines += [describe(c) for c in self.control]
        return "\n".join(lines)

    def find(self, name: str) -> Optional[Command]:
        """The command called `name` ("!join", "join", "next agent", "[claim]"), or None."""
        wanted = name.strip().lstrip("!").strip("[]").strip().lower()
        for registry in (self.typed, self.spoken, self.control):
            for command in registry:
                if wanted in (command.name.lower(), *(alias.lower() for alias in command.aliases)):
                    return command
        return None
