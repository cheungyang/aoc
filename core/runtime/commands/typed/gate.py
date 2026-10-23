"""`!gate review [all]`: the last day's utterances worth a check, posted for
your ✅ or ❌ (the review is core/voice/listening/gate_commands.py's)."""
from core.runtime.commands.base import TypedContext, VoiceRoomCommand


class GateCommand(VoiceRoomCommand):
    name = "gate"
    summary = "Posts the last day's utterances worth a check, for your ✅ or ❌ (`!gate review all`: every one)."
    usage = "review [all]"
    voice_host_only = True

    async def run(self, ctx: TypedContext, args: str) -> None:
        await ctx.voice.gate(args, ctx.channel)
