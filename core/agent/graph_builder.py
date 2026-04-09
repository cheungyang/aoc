import contextlib
import os
import json
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from core.memory.flat_file_checkpointer import FlatFileCheckpointer
from langchain_mcp_adapters.tools import load_mcp_tools
from core.loaders.tools_loader import ToolsLoader
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from core.loaders.skills_loader import SkillsLoader
from core.loaders.agents_loader import AgentsLoader
from core.util import get_knowledge_prompt

class GraphBuilder:
    def __init__(self):
        pass

    def _get_prompt_template(self, agent_id):
        # 1. Agent Prompt
        agents_loader = AgentsLoader()
        agent_prompt = agents_loader.get_agent_prompt(agent_id)

        # 2. Skills Prompt
        skills_loader = SkillsLoader()
        skills_prompt = skills_loader.get_skills_overview(agent_id=agent_id)

        # 3. Knowledge Prompt
        knowledge_str = get_knowledge_prompt()

        prompt = ChatPromptTemplate.from_messages([
            ("system", agent_prompt),
            ("system", skills_prompt),
            ("system", knowledge_str),
            MessagesPlaceholder(variable_name="messages"),
        ])
        return prompt

    async def build_graph(self, agent_id, config):
        if config is None:
            raise ValueError(f"Agent configuration not found for: {agent_id}")

        agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agents"))
        agent_path = os.path.join(agents_dir, agent_id)

        model_name = config.get("model", "gemini-3-flash-preview")
        
        loader = ToolsLoader()
        allowed_tools = loader.get_tools(agent_id=agent_id)

        prompt = self._get_prompt_template(agent_id)

        llm = ChatGoogleGenerativeAI(model=model_name)
        checkpointer = FlatFileCheckpointer()
        graph = create_react_agent(llm, allowed_tools, prompt=prompt, checkpointer=checkpointer)
        print(f"New Graph for {agent_id} built")
        return graph
