"""`!new`: a fresh start for this channel's session; its history is archived.
Anyone the bots know may give it."""
from core.runtime.commands.base import ANYONE, ChannelCommand, TypedContext
from core.runtime.foundation.sessions import SessionManager


class NewCommand(ChannelCommand):
    name = "new"
    summary = "Clears this channel's session; its history is archived."
    needs = ANYONE

    async def run(self, ctx: TypedContext, args: str) -> None:
        archive_status = SessionManager().clear_session(ctx.session)
        await ctx.send(f"Session context cleared. {archive_status}")
