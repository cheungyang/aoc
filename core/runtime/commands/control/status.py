"""`!status`: each instance says who holds which agent."""
from core.runtime.commands.base import ControlCommand, ControlContext


class StatusCommand(ControlCommand):
    name = "status"
    summary = "Each instance says who holds which agent."

    async def run(self, ctx: ControlContext, args: str) -> None:
        if ctx.control.elected():
            await ctx.send(ctx.control.describe())
