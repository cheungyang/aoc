from abc import ABC, abstractmethod
from typing import List, Optional, Any, Union
from core.runtime.execution_context import ExecutionContext

class BaseAgent(ABC):
    def __init__(self, agent_id: str, config: dict):
        self.agent_id = agent_id
        self.config = config

    @abstractmethod
    async def execute(
        self,
        prompt: Union[str, list],
        session: ExecutionContext,
        callbacks: Optional[List] = None,
        role: str = "user"
    ) -> str:
        """
        Execute the agent with the given prompt and ExecutionContext.
        """
        pass

    async def execute_stream(
        self,
        prompt: Union[str, list],
        session: ExecutionContext,
        callbacks: Optional[List] = None,
        role: str = "user"
    ):
        """Default streaming implementation that falls back to execute."""
        result = await self.execute(prompt, session=session, callbacks=callbacks, role=role)
        yield {"type": "token", "content": result}
        yield {"type": "final_response", "text": result}

    def get_config(self, key, default_value=None):
        return self.config.get(key, default_value)
