"""`!voiceprint [reset]`: the voiceprint gate's state and your voiceprint
(the reports are core/voice/listening/gate_commands.py's)."""
from core.runtime.commands.base import TypedContext, VoiceRoomCommand


class VoiceprintCommand(VoiceRoomCommand):
    name = "voiceprint"
    summary = "Shows the voiceprint gate and your voiceprint; `!voiceprint reset` deletes it."
    usage = "[reset]"
    voice_host_only = True

    async def run(self, ctx: TypedContext, args: str) -> None:
        await ctx.reply(ctx.voice.voiceprint(args))
