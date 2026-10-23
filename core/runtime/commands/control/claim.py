"""`[claim <agent>]`: the dev instance takes <agent>. If no dev instance is
running, prod still goes quiet for it (ownership.py's MANUAL hold)."""
import re
from typing import Optional

from core.runtime.commands.base import ControlCommand, ControlContext

_CMD_CLAIM_RE = re.compile(r"^\[\s*claim\s+([\w\-]+)\s*\]$", re.IGNORECASE)


class ClaimCommand(ControlCommand):
    name = "claim"
    usage = "[claim <agent>]"
    summary = "Hands <agent> to the dev instance; prod goes quiet for it even if no dev instance is running."

    def match(self, text: str) -> Optional[str]:
        found = _CMD_CLAIM_RE.match((text or "").strip())
        return found.group(1) if found else None

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
