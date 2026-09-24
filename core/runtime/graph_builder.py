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
from core.util import get_knowledge_prompt, get_formatting_prompt, get_channel_prompt, Config, resolve_model
from core.util.prompt_util import get_agent_static_prompt, get_agent_memory_prompt
from langgraph.types import interrupt
from core.runtime.job_manager import JobManager
from core.runtime.execution_context import try_context

class GraphBuilder:
    def __init__(self):
        pass

    def _get_prompt_template(self, ctx):
        agent_id = ctx.agent_id

        def dynamic_prompt(state):
            # 1. Agent Prompt, split for caching: the checked-in definition
            # (static) and the PKM memory files (mutable at runtime).
            agent_static_prompt = get_agent_static_prompt(agent_id)
            agent_memory_prompt = get_agent_memory_prompt(agent_id)

            # 2. Skills Prompt
            skills_loader = SkillsLoader()
            skills_prompt = skills_loader.get_skills_overview(ctx)

            # 2.5. Subgraphs Prompt
            from core.loaders.graphs_loader import GraphsLoader
            graphs_loader = GraphsLoader()
            subgraphs_prompt = graphs_loader.get_graphs_overview(ctx)

            # 3. Knowledge Prompt
            knowledge_prompt = get_knowledge_prompt()

            # 4. Channel Prompt
            channel_prompt = get_channel_prompt()

            # 5. Formatting Prompt
            formatting_prompt = get_formatting_prompt()

            # Order from most static to most dynamic to maximize prefix prompt caching.
            # Every block is a part of one system_instruction; tool schemas are NOT
            # in this list -- the LLM client sends them in a separate request field.
            #   formatting, agent definition, skills, subgraphs  -> change on deploy
            #   knowledge                                        -> changes once a day
            #   agent memory (HUMAN_CONTEXT/MEMORY/FEEDBACK)     -> rewritten by memory/dream
            #   channel                                          -> differs per channel/thread
            # Memory precedes channel: memory is identical across all of an agent's
            # channels and changes rarely, so channel-varying calls (e.g. agent_call
            # into another channel) still share the prefix up to and including memory.
            system_messages = [
                ("system", formatting_prompt),
                ("system", agent_static_prompt.replace("{", "{{").replace("}", "}}")),
                ("system", skills_prompt.replace("{", "{{").replace("}", "}}")),
                ("system", subgraphs_prompt.replace("{", "{{").replace("}", "}}")),
                ("system", knowledge_prompt.replace("{", "{{").replace("}", "}}")),
                ("system", agent_memory_prompt.replace("{", "{{").replace("}", "}}")),
                ("system", channel_prompt.replace("{", "{{").replace("}", "}}")),
            ]

            # Filter out empty prompt messages (e.g. when channel_prompt is empty)
            system_messages = [msg for msg in system_messages if msg[1].strip()]
            system_messages.append(MessagesPlaceholder(variable_name="messages"))

            prompt = ChatPromptTemplate.from_messages(system_messages)
            return prompt.format_messages(messages=state.get("messages", []))
        return dynamic_prompt

    async def build_graph(self, ctx, config):
        if config is None:
            raise ValueError(f"Agent configuration not found for: {getattr(ctx, 'agent_id', None)}")

        agent_id = ctx.agent_id
        agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agents"))
        agent_path = os.path.join(agents_dir, agent_id)

        provider = config.get("provider", "google")
        model_name = resolve_model(config.get("model"), provider=provider)
        
        loader = ToolsLoader()
        allowed_tools = loader.get_tools(ctx)

        def make_interruptible(t):
            import functools

            # Wrap a per-build copy. The tools returned by ToolsLoader are module-level @tool
            # singletons shared by every agent; mutating them in place made the wrappers nest
            # and accumulate on each build.
            try:
                t = t.model_copy()
            except AttributeError:
                import copy as _copy
                t = _copy.copy(t)

            original_run = t._run
            original_arun = t._arun

            def _abort_if_killed():
                active_sess = try_context()
                job_id = active_sess.job_id if active_sess else None
                if job_id:
                    job = JobManager()._jobs.get(job_id)
                    if job and job.status == "killing":
                        JobManager().update_job(job_id, "killed")
                        interrupt("Job was killed")

            @functools.wraps(original_run)
            def wrapper(*args, **kwargs):
                _abort_if_killed()
                return original_run(*args, **kwargs)
            
            t._run = wrapper
            
            if original_arun is not None:
                @functools.wraps(original_arun)
                async def awrapper(*args, **kwargs):
                    _abort_if_killed()
                    return await original_arun(*args, **kwargs)
                t._arun = awrapper
                
            return t

        allowed_tools = [make_interruptible(t) for t in allowed_tools]

        if provider == "ollama":
            from langchain_ollama import ChatOllama
            llm = ChatOllama(model=model_name)
        elif provider == "local":
            # An on-device server speaking the OpenAI wire format. ChatOpenAI is
            # a protocol client here, not a route to the hosted API.
            from langchain_openai import ChatOpenAI

            cfg = Config()
            llm = ChatOpenAI(
                model=model_name,
                base_url=cfg.local_llm_base_url,
                # Load-bearing, despite the server ignoring it. Omitting the
                # argument makes ChatOpenAI fall back to OPENAI_API_KEY from the
                # environment -- which .env exports -- so a machine holding a
                # real key would post that paid credential to an
                # unauthenticated local socket. A constant makes that
                # impossible. The client also rejects an empty string, so this
                # cannot simply be "".
                api_key="not-needed",
                timeout=cfg.local_llm_timeout,
                # The client's default of 2 assumes an elastic hosted service.
                # This server handles one request at a time, so a retry does not
                # find spare capacity -- it joins the back of the queue every
                # other agent is already waiting in.
                max_retries=0,
                # Sends stream_options.include_usage, without which the usage
                # metadata LoggingHandler records is absent under streaming.
                stream_usage=True,
            )
        else:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model=model_name)
        checkpointer = SqliteCheckpointer()

        prompt = self._get_prompt_template(ctx)

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
        # print(f"New Graph '{graph_name}' for {agent_id} built")
        return graph
