import contextlib
import os
import json
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from core.hook_loader import HookLoader
from core.memory.flat_file_checkpointer import FlatFileCheckpointer
from core.agent import Agent
from langchain_mcp_adapters.tools import load_mcp_tools

class AgentBuilder:
    def __init__(self, mcp_session):
        self.mcp_session = mcp_session

    def list_agents(self):
        agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agents"))
        agents_list = []
        if not os.path.exists(agents_dir):
            return agents_list
        for agent_name in os.listdir(agents_dir):
            agent_path = os.path.join(agents_dir, agent_name)
            if os.path.isdir(agent_path):
                config_path = os.path.join(agent_path, "agent.json")
                if os.path.exists(config_path):
                    try:
                        with open(config_path, "r") as f:
                            config = json.load(f)
                        agents_list.append({
                            "name": config.get("name", agent_name),
                            "emoji": config.get("emoji", ""),
                            "description": config.get("description", ""),
                            "id": agent_name
                        })
                    except Exception as e:
                        print(f"Error loading config for {agent_name}: {e}")
        return agents_list

    async def build_agent(self, agent_name="concierge"):
        agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agents"))
        agent_path = os.path.join(agents_dir, agent_name)
        if not os.path.isdir(agent_path):
            raise ValueError(f"Agent folder not found: {agent_name}")

        config_path = os.path.join(agent_path, "agent.json")
        if not os.path.exists(config_path):
             raise ValueError(f"agent.json not found for: {agent_name}")

        with open(config_path, "r") as f:
            config = json.load(f)

        model_name = config.get("model", "gemini-3-flash-preview")
        allowed_tools = config.get("tools", [])
        allowed_skills = config.get("skills", [])

        # Configure session visibility on the server
        try:
            print(f"Configuring session for agent {agent_name} with tools: {allowed_tools}")
            await self.mcp_session.call_tool("configure_session", arguments={"allowed_tools": allowed_tools})
        except Exception as e:
            print(f"Failed to configure session visibility: {e}")

        # Load tools from session (now filtered by server)
        tools = await load_mcp_tools(self.mcp_session)
        print(f"Loaded tools for agent: {[getattr(t, 'name', None) for t in tools]}")

        system_prompt = ""

        hook_loader = HookLoader()
        for hook in hook_loader.system_prompt_hooks:
            system_prompt = hook(system_prompt, config=config, agent_path=agent_path)

        llm = ChatGoogleGenerativeAI(model=model_name)
        checkpointer = FlatFileCheckpointer()
        graph = create_react_agent(llm, tools, prompt=system_prompt, checkpointer=checkpointer)
        return Agent(graph)

