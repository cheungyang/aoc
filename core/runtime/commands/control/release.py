"""`[release]`: every agent back to prod."""
import re
from typing import Optional

from core.runtime.commands.base import ControlCommand, ControlContext

_CMD_RELEASE_RE = re.compile(r"^\[\s*release\s*\]$", re.IGNORECASE)


class ReleaseCommand(ControlCommand):
    name = "release"
    usage = "[release]"
    summary = "Hands every agent back to prod."

    def match(self, text: str) -> Optional[str]:
        return "" if _CMD_RELEASE_RE.match((text or "").strip()) else None

    async def run(self, ctx: ControlContext, args: str) -> None:
        if ctx.control.elected():
            await ctx.control.react("✅")
