"""How an agent is built from its config.

AgentsLoader (core/loaders) reads the configs and hands out the agents, but the
loaders never import the runtime, so core/runtime/bootstrap.py plugs
`build_agent` into it.
"""
from typing import Any, Dict

from core.runtime.agent import Agent
from core.runtime.base_agent import BaseAgent
from core.runtime.script_executor import ScriptExecutorAgent

SCRIPT_EXECUTOR = "script-executor"


def build_agent(agent_id: str, config: Dict[str, Any]) -> BaseAgent:
    """The agent `config` describes. The script executor runs scripts without a
    model; every other agent is an Agent."""
    if agent_id == SCRIPT_EXECUTOR:
        return ScriptExecutorAgent(agent_id, config)
    return Agent(agent_id, config)
