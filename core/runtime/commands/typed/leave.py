"""`!leave`: the bot leaves the voice room."""
from core.runtime.commands.base import TypedContext, VoiceRoomCommand


class LeaveCommand(VoiceRoomCommand):
    name = "leave"
    summary = "Leaves the voice room."

    async def run(self, ctx: TypedContext, args: str) -> None:
        await ctx.voice.leave()
        await ctx.send("Disconnected from voice channel. 👋")
