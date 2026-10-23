"""Wait: "wait", "hold on". Said on its own, it stops the reply but keeps what
you said before for what you say next (its phrases are in stop.py)."""
from typing import Optional

from core.runtime.commands.base import SpokenCommand, SpokenContext
from core.runtime.commands.spoken.stop import bare_stop
from core.runtime.observability.turn_trace import Outcome


class WaitCommand(SpokenCommand):
    name = "wait"
    summary = "Stops the reply, and keeps what you said for what you say next."
    examples = ("wait", "hold on", "hang on")
    outcome = Outcome.STOP_HOLD

    def match(self, transcript: str) -> Optional[str]:
        return "" if bare_stop(transcript) == "hold" else None

    async def run(self, ctx: SpokenContext, args: str) -> bool:
        ctx.log(f"✋ Heard {ctx.heard!r}: stopping, keeping the request for what comes next.")
        ctx.turn.hold()
        return True
