import contextlib
import os
import json
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from core.hook_loader import HookLoader
from core.memory.flat_file_checkpointer import FlatFileCheckpointer
from .agent import Agent
from langchain_mcp_adapters.tools import load_mcp_tools

class AgentBuilder:
    def __init__(self, mcp_session):
        self.mcp_session = mcp_session

    async def build_agent(self, agent_id, config):
        if config is None:
            raise ValueError(f"Agent configuration not found for: {agent_id}")

        agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agents"))
        # Assume folder name matches agent_id
        agent_path = os.path.join(agents_dir, agent_id)

        model_name = config.get("model", "gemini-3-flash-preview")
        allowed_tools = list(config.get("tools", {}).keys())
        allowed_skills = config.get("skills", [])

        # Configure session visibility on the server
        try:
            print(f"Configuring session for agent {agent_id} with tools: {allowed_tools}")
            await self.mcp_session.call_tool("configure_session", arguments={"allowed_tools": allowed_tools})
        except Exception as e:
            print(f"Failed to configure session visibility: {e}")

        # Load tools from session (now filtered by server)
        tools = await load_mcp_tools(self.mcp_session)
        print(f"Loaded tools for agent {agent_id}: {[getattr(t, 'name', None) for t in tools]}")

        system_prompt = ""

        hook_loader = HookLoader()
        for hook in hook_loader.system_prompt_hooks:
            system_prompt = hook(system_prompt, config=config, agent_path=agent_path)

        llm = ChatGoogleGenerativeAI(model=model_name)
        checkpointer = FlatFileCheckpointer()
        graph = create_react_agent(llm, tools, prompt=system_prompt, checkpointer=checkpointer)
        agent = Agent(graph)
        print(f"New Agent {agent_id} built")
        return agent
