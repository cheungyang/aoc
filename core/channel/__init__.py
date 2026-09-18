"""Channel layer: base protocols, response parser, and channel adapters."""
from core.channel.base import BaseChannel
from core.channel.response_parser import AgentResponse

__all__ = [
    "BaseChannel",
    "AgentResponse",
]
