"""A Discord channel or thread as a turn's output (core/runtime/foundation/output.py).

The Discord runner, the scheduler and the host of the voice room pass one when
they start a turn. Rendering is the dispatcher's, as before: what it posts
still depends on where the turn came from (`ctx.source`).
"""
from typing import Any, Optional

from core.channel.discord.dispatcher import dispatch_discord_output


class DiscordOutput:
    """Writes a turn's output to `channel`, a Discord channel or thread."""

    def __init__(self, channel: Any):
        self.channel = channel

    async def deliver(self, ctx: Any, response: Any) -> None:
        await dispatch_discord_output(channel=self.channel, response=response, source=ctx.source)

    async def notify(self, text: str) -> None:
        await self.channel.send(text)

    def to(self, channel: str) -> Optional["DiscordOutput"]:
        """Another channel, found by name or id among the running bots' guilds."""
        # The loader imports the runner, which builds these.
        from core.channel.discord.loader import BotsLoader

        found = BotsLoader().find_channel(channel)
        return DiscordOutput(found) if found is not None else None
