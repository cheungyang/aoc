"""`!enroll [cancel]`: takes your voiceprint, typed while you're in the voice
channel (the flow is core/voice/listening/enrollment.py's EnrollmentFlow)."""
from core.runtime.commands.base import TypedContext, VoiceRoomCommand


class EnrollCommand(VoiceRoomCommand):
    name = "enroll"
    summary = "Takes your voiceprint while you're in the voice channel; `!enroll cancel` stops."
    usage = "[cancel]"
    voice_host_only = True

    async def run(self, ctx: TypedContext, args: str) -> None:
        await ctx.reply(await ctx.voice.enroll(args, ctx.channel))
