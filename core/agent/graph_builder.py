import contextlib
import os
import json
from langgraph.prebuilt import create_react_agent
from core.memory.flat_file_checkpointer import FlatFileCheckpointer
from langchain_mcp_adapters.tools import load_mcp_tools
from core.loaders.tools_loader import ToolsLoader
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from core.loaders.skills_loader import SkillsLoader
from core.loaders.agents_loader import AgentsLoader
from core.util import get_knowledge_prompt, get_formatting_prompt, get_agent_prompt
from langgraph.types import interrupt
from core.agent.job_manager import JobManager, current_job_id

class GraphBuilder:
    def __init__(self):
        pass

    def _get_prompt_template(self, agent_id):
        # 1. Agent Prompt
        agent_prompt = get_agent_prompt(agent_id)

        # 2. Skills Prompt
        skills_loader = SkillsLoader()
        skills_prompt = skills_loader.get_skills_overview(agent_id=agent_id)

        # 3. Knowledge Prompt
        knowledge_prompt = get_knowledge_prompt()

        # 4. Formatting Prompt
        formatting_prompt = get_formatting_prompt()

        prompt = ChatPromptTemplate.from_messages([
            ("system", agent_prompt),
            ("system", skills_prompt),
            ("system", knowledge_prompt),
            ("system", formatting_prompt),
            MessagesPlaceholder(variable_name="messages"),
        ])
        return prompt

    async def build_graph(self, agent_id, config):
        if config is None:
            raise ValueError(f"Agent configuration not found for: {agent_id}")

        agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agents"))
        agent_path = os.path.join(agents_dir, agent_id)

        model_name = config.get("model", "gemini-3-flash-preview")
        provider = config.get("provider", "google")
        
        loader = ToolsLoader()
        allowed_tools = loader.get_tools(agent_id=agent_id)

        def make_interruptible(t):
            original_run = t._run
            original_arun = t._arun
            
            def wrapper(*args, **kwargs):
                job_id = current_job_id.get()
                if job_id:
                    job = JobManager()._jobs.get(job_id)
                    if job and job.status == "killing":
                        JobManager().update_job(job_id, "killed")
                        interrupt("Job was killed")
                return original_run(*args, **kwargs)
            
            async def awrapper(*args, **kwargs):
                job_id = current_job_id.get()
                if job_id:
                    job = JobManager()._jobs.get(job_id)
                    if job and job.status == "killing":
                        JobManager().update_job(job_id, "killed")
                        interrupt("Job was killed")
                return await original_arun(*args, **kwargs)

            t._run = wrapper
            if original_arun is not None:
                t._arun = awrapper
            return t

        allowed_tools = [make_interruptible(t) for t in allowed_tools]

        prompt = self._get_prompt_template(agent_id)

        if provider == "ollama":
            from langchain_ollama import ChatOllama
            llm = ChatOllama(model=model_name)
        else:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model=model_name)
        checkpointer = FlatFileCheckpointer()
        graph = create_react_agent(llm, allowed_tools, prompt=prompt, checkpointer=checkpointer)
        print(f"New Graph for {agent_id} built")
        return graph
