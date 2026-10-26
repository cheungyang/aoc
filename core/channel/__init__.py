"""Channel layer: base protocols, the response parser, channel names (names.py), the channel inbox's writes (inbox.py), Discord-sized chunks (chunks.py), and the channel adapters."""
from core.channel.base import BaseChannel
from core.channel.response_parser import AgentResponse

__all__ = [
    "BaseChannel",
    "AgentResponse",
]
