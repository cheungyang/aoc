"""A Discord channel or thread as a turn's output (core/runtime/foundation/output.py).

The Discord runner, the scheduler and the host of the voice room pass one when
they start a turn. Rendering is the dispatcher's, as before: what it posts
still depends on where the turn came from (`ctx.source`).

Only the runner's on_message passes `message_id`, your message the turn
answers. `source_message` keeps it, and the proactive taps note it, so a live
`now` route can put its recipient's emoji on that message (core/proactive/pipeline.py).
"""
from typing import Any, Optional

from core.channel.discord.dispatcher import dispatch_discord_output


class DiscordOutput:
    """Writes a turn's output to `channel`, a Discord channel or thread."""

    def __init__(self, channel: Any, message_id: Any = None):
        self.channel = channel
        # "<channel id>/<message id>": your message that started the turn, or None.
        channel_id = getattr(channel, "id", None)
        self.source_message: Optional[str] = (f"{channel_id}/{message_id}"
                                              if message_id is not None and channel_id is not None else None)

    async def deliver(self, ctx: Any, response: Any) -> None:
        await dispatch_discord_output(channel=self.channel, response=response, source=ctx.source)

    async def notify(self, text: str) -> None:
        await self.channel.send(text)

    def to(self, channel: str) -> Optional["DiscordOutput"]:
        """Another channel, found by name or id among the running bots' guilds. It keeps
        `source_message`: a turn delegated there still started from your message."""
        # The loader imports the runner, which builds these.
        from core.channel.discord.loader import BotsLoader

        found = BotsLoader().find_channel(channel)
        if found is None:
            return None
        other = DiscordOutput(found)
        other.source_message = self.source_message
        return other
