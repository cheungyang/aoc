import contextlib
import os
import json
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from core.loaders.hooks_loader import HooksLoader
from core.memory.flat_file_checkpointer import FlatFileCheckpointer
from langchain_mcp_adapters.tools import load_mcp_tools
from core.loaders.tools_loader import ToolsLoader

class GraphBuilder:
    def __init__(self):
        pass

    async def build_graph(self, agent_id, config):
        if config is None:
            raise ValueError(f"Agent configuration not found for: {agent_id}")

        agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agents"))
        agent_path = os.path.join(agents_dir, agent_id)

        model_name = config.get("model", "gemini-3-flash-preview")
        
        loader = ToolsLoader()
        allowed_tools = loader.get_tools(list(config.get("tools", {}).keys()))

        system_prompt = ""
        hook_loader = HooksLoader()
        for hook in hook_loader.system_prompt_hooks:
            system_prompt = hook(system_prompt, config=config, agent_path=agent_path)

        llm = ChatGoogleGenerativeAI(model=model_name)
        checkpointer = FlatFileCheckpointer()
        graph = create_react_agent(llm, allowed_tools, prompt=system_prompt, checkpointer=checkpointer)
        print(f"New Graph for {agent_id} built")
        return graph
