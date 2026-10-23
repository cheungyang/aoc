"""`!join [voice channel]`: the bot joins the voice room, starting in the
conversation of the text channel it was typed in."""
from core.runtime.commands.base import TypedContext, VoiceRoomCommand


class JoinCommand(VoiceRoomCommand):
    name = "join"
    summary = "Joins the voice room (or the voice channel named), talking in this channel's conversation."
    usage = "[voice channel]"

    async def run(self, ctx: TypedContext, args: str) -> None:
        # Typed in #day-planning, this joins the voice room and starts you
        # with Daisy: the channel it was typed in becomes the focus.
        target = args or None
        voice = getattr(ctx.author, "voice", None)
        if not target and voice:
            target = str(voice.channel.id)
        success = await ctx.voice.join(target, focus_channel=ctx.channel)
        if success:
            focus = ctx.voice.focus()
            where = "the lobby" if focus.is_lobby else f"#{focus.channel_name}"
            await ctx.send(f"Connected to voice channel: `{ctx.voice.room().name}`, talking in {where} 🎙️")
        else:
            await ctx.send("Could not join voice channel.")
