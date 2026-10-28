"""The `now` reaction on Discord: the recipient's emoji on your message, at once.

When a live decision approves a `now` route, the proactive pipeline
(core/proactive/pipeline.py) reacts on your message that started it, before
the recipient's post arrives: the receipt gets 🧑‍🍳 seconds after it's
saved, and Ramsay's post follows. This module is that reaction's Discord side,
the pipeline's `ack`.

The message is "<channel id>/<message id>" (DiscordOutput.source_message). Its
channel is found through the bots: the recipient's own bot if this host runs
it and it can see the channel, else main's. Reacting needs no fetch: it's one
call on a partial message. Errors are logged, never raised.
"""
from typing import Any, Optional

# The bot that reacts when the recipient's can't.
HOST_AGENT = "main"


class DiscordAck:
    """Puts a recipient's emoji on a Discord message (the pipeline's Ack)."""

    def __init__(self, bots: Any = None):
        self._bots = bots

    async def react(self, message: str, emoji: str, agent_id: str) -> bool:
        """Puts `emoji` on `message` as `agent_id`'s bot, or main's; True if it did. Never raises."""
        try:
            channel_id, message_id = (int(part) for part in str(message).split("/"))
            channel = self._channel(channel_id, agent_id)
            if channel is None:
                print(f"[proactive] No {emoji} on {message}: no bot here sees channel {channel_id}.")
                return False
            await channel.get_partial_message(message_id).add_reaction(emoji)
            return True
        except Exception as e:
            print(f"[proactive] Could not react {emoji} on {message}: {type(e).__name__}: {e}")
            return False

    def _channel(self, channel_id: int, agent_id: str) -> Optional[Any]:
        """The channel, from the first bot that runs here, is connected and sees it: the
        recipient's, then main's."""
        # Imported here: the loader imports the runner, which builds the outputs.
        from core.channel.discord.loader import BotsLoader

        bots = self._bots or BotsLoader()
        for owner in dict.fromkeys((agent_id, HOST_AGENT)):
            try:
                client = getattr(bots.get_bot(owner), "bot", None)
                channel = client.get_channel(channel_id) if client is not None and client.is_ready() else None
            except Exception as e:
                print(f"[proactive] {owner}'s bot can't look up channel {channel_id}: {type(e).__name__}: {e}")
                continue
            if channel is not None:
                return channel
        return None
