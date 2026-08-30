from abc import ABC, abstractmethod
import discord
from typing import List, Optional, Any, Union
from core.agent.session_identifier import SessionIdentifier

class BaseAgent(ABC):
    def __init__(self, agent_id: str, config: dict):
        self.agent_id = agent_id
        self.config = config

    @abstractmethod
    async def execute(
        self,
        prompt: Union[str, list],
        session: SessionIdentifier,
        callbacks: Optional[List] = None,
        role: str = "user"
    ) -> str:
        """
        Execute the agent with the given prompt and SessionIdentifier.
        """
        pass

    def get_config(self, key, default_value=None):
        return self.config.get(key, default_value)
