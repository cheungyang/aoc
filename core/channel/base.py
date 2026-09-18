"""Base protocols and types for channel communication."""
from typing import Protocol, Optional, List, Dict, Any, runtime_checkable


@runtime_checkable
class BaseChannel(Protocol):
    """Abstract interface for a chat channel (text, thread, etc.)."""

    @property
    def name(self) -> str:
        """Channel name (e.g. 'software-dev')."""
        ...

    @property
    def id(self) -> Any:
        """Unique channel identifier."""
        ...

    async def send(self, content: str, **kwargs: Any) -> Any:
        """Sends a message to the channel."""
        ...
