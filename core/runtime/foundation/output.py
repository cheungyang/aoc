"""Where a turn's output goes: the port an adapter fills when it starts a turn.

The runtime never renders. The adapter that mints a context (the Discord
runner, the scheduler, the host of the voice room) passes a TurnOutput to
`SessionManager.get_session`, and the turn hands it what to show:

  deliver(ctx, response)  a batch turn's reply. Streamed turns are rendered by
                          whoever reads the stream, as before.
  notify(text)            an error, or the empty-prompt warning
  to(channel)             the same surface, another channel: where a turn
                          delegated to that channel's agent writes. None when
                          the surface can't find it.
  channel                 what it writes to, as sessions key on it

Optional, read with getattr:

  source_message          the message of yours that started the turn, as the
                          surface names it ("<channel id>/<message id>" on
                          Discord), or None. The proactive taps keep it, for a
                          live `now` route's reaction (core/proactive/pipeline.py).

Derived contexts keep their output, and delegation hands the caller's on (or
`to()` another channel), so a turn always writes where its surface is.
"""
from typing import Any, Optional, Protocol


class TurnOutput(Protocol):
    channel: Any

    async def deliver(self, ctx: Any, response: Any) -> None: ...

    async def notify(self, text: str) -> None: ...

    def to(self, channel: str) -> Optional["TurnOutput"]: ...
