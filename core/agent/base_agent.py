from abc import ABC, abstractmethod
import discord
from typing import List

class BaseAgent(ABC):
    def __init__(self, agent_id: str, config: dict):
        self.agent_id = agent_id
        self.config = config

    @abstractmethod
    async def execute(self, content: str, source: str, job_id: str = None, channel: discord.TextChannel = None, callbacks: List = None, role: str = "user") -> str:
        """
        Execute the agent with the given content.
        """
        pass

    def get_config(self, key, default_value=None):
        return self.config.get(key, default_value)
