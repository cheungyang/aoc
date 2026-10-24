"""`!claim <agent>`: the dev instance takes <agent>. If no dev instance is
running, prod still goes quiet for it (ownership.py's MANUAL hold)."""
import re

from core.runtime.commands.base import ControlCommand, ControlContext

_AGENT = re.compile(r"[\w\-]+")


class ClaimCommand(ControlCommand):
    name = "claim"
    usage = "<agent>"
    summary = "Hands <agent> to the dev instance; prod goes quiet for it even if no dev instance is running."

    def takes(self, args: str) -> bool:
        return bool(_AGENT.fullmatch(args))

    async def run(self, ctx: ControlContext, agent: str) -> None:
        control = ctx.control
        if agent == ctx.agent_id:
            try:
                await control.claim()
                await control.react("✅")
            except Exception as e:
                print(f"[BotRunner:{ctx.agent_id}] ⚠️ could not claim agent: {e}")
                await control.react("⚠️")
        elif control.elected() and agent not in control.agent_ids():
            await control.react("❓")
