"""`[status]`: each instance says who holds which agent."""
import re
from typing import Optional

from core.runtime.commands.base import ControlCommand, ControlContext

_CMD_STATUS_RE = re.compile(r"^\[\s*status\s*\]$", re.IGNORECASE)


class StatusCommand(ControlCommand):
    name = "status"
    usage = "[status]"
    summary = "Each instance says who holds which agent."

    def match(self, text: str) -> Optional[str]:
        return "" if _CMD_STATUS_RE.match((text or "").strip()) else None

    async def run(self, ctx: ControlContext, args: str) -> None:
        if ctx.control.elected():
            await ctx.send(ctx.control.describe())
