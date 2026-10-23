"""`!restart`: restarts the whole process, every agent and bot with it."""
import asyncio
import os
import sys

from core.runtime.commands.base import ChannelCommand, TypedContext


class RestartCommand(ChannelCommand):
    name = "restart"
    summary = "Restarts the process: every agent and every bot."

    async def run(self, ctx: TypedContext, args: str) -> None:
        await ctx.send("System is restarting...")
        await asyncio.sleep(0.5)
        os.execv(sys.executable, [sys.executable] + sys.argv)
