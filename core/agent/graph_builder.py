import contextlib
import os
import json
from langgraph.prebuilt import create_react_agent
from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
from langchain_mcp_adapters.tools import load_mcp_tools
from core.loaders.tools_loader import ToolsLoader
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from core.loaders.skills_loader import SkillsLoader
from core.loaders.agents_loader import AgentsLoader
from core.util import get_knowledge_prompt, get_formatting_prompt, get_agent_prompt, get_channel_prompt, Config
from langgraph.types import interrupt
from core.agent.job_manager import JobManager, current_session_identifier

class GraphBuilder:
    def __init__(self):
        pass

    def _get_prompt_template(self, agent_id):
        def dynamic_prompt(state):
            # 1. Agent Prompt
            agent_prompt = get_agent_prompt(agent_id)

            # 2. Skills Prompt
            skills_loader = SkillsLoader()
            skills_prompt = skills_loader.get_skills_overview(agent_id=agent_id)

            # 2.5. Subgraphs Prompt
            from core.loaders.graphs_loader import GraphsLoader
            graphs_loader = GraphsLoader()
            subgraphs_prompt = graphs_loader.get_graphs_overview(agent_id=agent_id)

            # 3. Knowledge Prompt
            knowledge_prompt = get_knowledge_prompt()

            # 4. Channel Prompt
            channel_prompt = get_channel_prompt()

            # 5. Formatting Prompt
            formatting_prompt = get_formatting_prompt()

            # Order from most static to most dynamic to maximize prefix prompt caching
            system_messages = [
                ("system", formatting_prompt),
                ("system", agent_prompt.replace("{", "{{").replace("}", "}}")),
                ("system", skills_prompt.replace("{", "{{").replace("}", "}}")),
                ("system", subgraphs_prompt.replace("{", "{{").replace("}", "}}")),
                ("system", knowledge_prompt.replace("{", "{{").replace("}", "}}")),
                ("system", channel_prompt.replace("{", "{{").replace("}", "}}")),
            ]

            # Filter out empty prompt messages (e.g. when channel_prompt is empty)
            system_messages = [msg for msg in system_messages if msg[1]]
            system_messages.append(MessagesPlaceholder(variable_name="messages"))

            prompt = ChatPromptTemplate.from_messages(system_messages)
            return prompt.format_messages(messages=state.get("messages", []))
        return dynamic_prompt

    async def build_graph(self, agent_id, config):
        if config is None:
            raise ValueError(f"Agent configuration not found for: {agent_id}")

        agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agents"))
        agent_path = os.path.join(agents_dir, agent_id)

        model_name = config.get("model", "gemini-3.5-flash-lite")
        provider = config.get("provider", "google")
        
        loader = ToolsLoader()
        allowed_tools = loader.get_tools(agent_id=agent_id)

        def make_interruptible(t):
            import functools
            original_run = t._run
            original_arun = t._arun
            
            @functools.wraps(original_run)
            def wrapper(*args, **kwargs):
                active_sess = current_session_identifier.get()
                job_id = active_sess.job_id if active_sess else None
                if job_id:
                    job = JobManager()._jobs.get(job_id)
                    if job and job.status == "killing":
                        JobManager().update_job(job_id, "killed")
                        interrupt("Job was killed")
                return original_run(*args, **kwargs)
            
            t._run = wrapper
            
            if original_arun is not None:
                @functools.wraps(original_arun)
                async def awrapper(*args, **kwargs):
                    active_sess = current_session_identifier.get()
                    job_id = active_sess.job_id if active_sess else None
                    if job_id:
                        job = JobManager()._jobs.get(job_id)
                        if job and job.status == "killing":
                            JobManager().update_job(job_id, "killed")
                            interrupt("Job was killed")
                    return await original_arun(*args, **kwargs)
                t._arun = awrapper
                
            return t

        allowed_tools = [make_interruptible(t) for t in allowed_tools]

        if provider == "ollama":
            from langchain_ollama import ChatOllama
            llm = ChatOllama(model=model_name)
        else:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model=model_name)
        checkpointer = SqliteCheckpointer()

        prompt = self._get_prompt_template(agent_id)

        graph_name = config.get("graph", "main")
        from core.loaders.graphs_loader import GraphsLoader
        graphs_loader = GraphsLoader()
        graph_info = graphs_loader.get_graph(graph_name)
        if not graph_info or "create_graph" not in graph_info or not graph_info["create_graph"]:
            raise ValueError(f"Graph '{graph_name}' not found or does not export create_graph.")

        create_graph_fn = graph_info["create_graph"]
        graph = create_graph_fn(
            llm=llm,
            tools=allowed_tools,
            prompt=prompt,
            checkpointer=checkpointer,
            agent_id=agent_id,
            config=config
        )
        print(f"New Graph '{graph_name}' for {agent_id} built")
        return graph
