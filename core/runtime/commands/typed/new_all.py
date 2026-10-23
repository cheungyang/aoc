"""`!newall`: a fresh start for every session; their histories are archived."""
from core.runtime.commands.base import ChannelCommand, TypedContext
from core.runtime.foundation.sessions import SessionManager


class NewAllCommand(ChannelCommand):
    name = "newall"
    summary = "Clears every session; their histories are archived."

    async def run(self, ctx: TypedContext, args: str) -> None:
        archive_status = SessionManager().clear_sessions()
        await ctx.reply(f"All session contexts cleared. {archive_status}", pause_s=1)
