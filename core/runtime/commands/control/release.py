"""`!release`: every agent back to prod."""
from core.runtime.commands.base import ControlCommand, ControlContext


class ReleaseCommand(ControlCommand):
    name = "release"
    summary = "Hands every agent back to prod."

    async def run(self, ctx: ControlContext, args: str) -> None:
        if ctx.control.elected():
            await ctx.control.react("✅")
