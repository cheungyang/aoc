from typing import List

from core.runtime.base_agent import BaseAgent
from core.runtime.execution_context import ExecutionContext

# Scheduled scripts run as subprocesses through `core.scheduler.script_runner`;
# nothing reaches this agent as a prompt in normal operation. The agent exists
# so `script-executor` owns the script schedules without a model behind it.
NO_PROMPTS = (
    "script-executor runs scheduled scripts only and does not accept prompts."
)


class ScriptExecutorAgent(BaseAgent):
    def __init__(self, agent_id: str, config: dict = None):
        super().__init__(agent_id, config or {})

    async def execute(
        self,
        prompt: str,
        session: ExecutionContext,
        callbacks: List = None,
        role: str = "user"
    ) -> str:
        """Refuses any prompt: a message routed or delegated here is a mistake,
        and the sender should be told rather than left without a reply."""
        if session.channel_obj is not None:
            await session.channel_obj.send(NO_PROMPTS)
        return NO_PROMPTS
