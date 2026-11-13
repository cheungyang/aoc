import contextlib
import os
import json
import sys
from typing import Optional, Tuple
from langgraph.prebuilt import create_react_agent
from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
from core.loaders.tools_loader import ToolsLoader
from langchain_core.messages import SystemMessage, convert_to_messages
from core.loaders.skills_loader import SkillsLoader
from core.loaders.agents_loader import AgentsLoader
from core.loaders.graphs_loader import GraphsLoader
from core.config.models import tier_and_level
from core.config.settings import Config
from core.prompts.system import (
    get_agent_memory_prompt,
    get_agent_static_prompt,
    get_channel_prompt,
    get_formatting_prompt,
    get_knowledge_prompt,
    get_tool_rules_prompt,
)
from core.prompts.live_context import get_live_context_prompt
from core.prompts.team import get_open_passes_prompt, get_team_prompt
from core.prompts.voice import get_voice_mode_prompt
from core.runtime.history.budget import cap_tool_output
from langgraph.types import interrupt
from core.runtime.foundation.jobs import JobManager
from core.runtime.foundation.context import try_context
from core.runtime.model.chat import chat_model

# The graph a turn with a light first call is built on: graphs/main, plus that call.
VOICE_GRAPH = "voice"

_warned_light_settings = set()


def light_call(ctx, config) -> Optional[Tuple[str, str]]:
    """The light model a turn's first model call runs on (graphs/voice):
    (tier, thinking level) from VOICE_LIGHT_MODEL, or None.

    Only a live voice turn of the agent you're talking to gets one: not a typed
    or scheduled turn, a stateless agent or context, an agent another agent
    called (`called_by_agent`), a graph step, an agent on a graph of its own,
    or one off Gemini. A setting that isn't `TIER:LEVEL` is reported once and
    counts as off. Keys the agent's compiled graphs too (core/runtime/agent.py).
    """
    setting = (Config().voice_light_model or "").strip()
    if not setting or setting.lower() == "off":
        return None
    if ctx is None or ctx.get_surface() != "voice" or ctx.is_stateless() or ctx.called_by_agent or ctx.graph_id:
        return None
    config = config or {}
    if config.get("stateless") or config.get("graph", "main") != "main" or config.get("provider", "google") != "google":
        return None
    try:
        return tier_and_level(setting)
    except ValueError as e:
        if setting not in _warned_light_settings:
            _warned_light_settings.add(setting)
            print(f"[GraphBuilder] VOICE_LIGHT_MODEL={setting!r} is off: {e}", file=sys.stderr)
        return None


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
            # What's true right now, from teammates' signals (empty with the switch off).
            live_context_prompt = get_live_context_prompt(agent_id)
            # How to treat what tools return (partial/failed, tool gaps); static.
            tool_rules_prompt = get_tool_rules_prompt(agent_id)

            # 2. Skills Prompt
            skills_loader = SkillsLoader()
            skills_prompt = skills_loader.get_skills_overview(ctx)

            # 2.5. Subgraphs Prompt
            graphs_loader = GraphsLoader()
            subgraphs_prompt = graphs_loader.get_graphs_overview(ctx)

            # 2.6. Team: the teammates the user can be handed to (empty for an
            # agent that doesn't talk with the user)
            team_prompt = get_team_prompt(agent_id)

            # 3. Knowledge Prompt
            knowledge_prompt = get_knowledge_prompt()

            # 4. Channel Prompt
            channel_prompt = get_channel_prompt()

            # 4.2. Open passes: what this agent passed to teammates from this
            # conversation that hasn't reached them yet (empty when nothing
            # waits). The channel is the live turn's, not `ctx`'s: a compiled
            # graph is reused across channels (core/runtime/agent.py). A
            # stateless run (a job, a dream) talks with no one, and gets none.
            live = try_context()
            open_passes_prompt = get_open_passes_prompt(
                agent_id, live.channel_name if live is not None and not live.is_stateless() else "")

            # 4.5. Voice mode: on turns the user will hear, the <speech> contract
            voice_mode_prompt = get_voice_mode_prompt()

            # 5. Formatting Prompt
            formatting_prompt = get_formatting_prompt()

            # Order from most static to most dynamic to maximize prefix prompt caching.
            # Every block is a part of one system_instruction; tool schemas are NOT
            # in this list -- the LLM client sends them in a separate request field.
            #   formatting, agent definition, tool rules,
            #   skills, subgraphs, team                          -> change on deploy
            #   knowledge                                        -> changes once a day
            #   agent memory (HUMAN_CONTEXT/MEMORY/FEEDBACK)     -> rewritten by memory/dream
            #   live context (teammates' live situations)        -> changes a few times a day
            #   channel                                          -> differs per channel/thread
            #   open passes                                      -> differs per conversation
            #   voice mode                                       -> voice turns only
            # Memory precedes channel: memory is identical across all of an agent's
            # channels and changes rarely, so channel-varying calls (e.g. agent_call
            # into another channel) still share the prefix up to and including memory.
            # Live context sits between them: it's the same in every channel too, but
            # changes more often than the nightly memory, so it comes after it.
            # Open passes follow the channel: they belong to one conversation, and
            # change when a pass is made or picked up.
            # Voice mode comes last, so a voice turn shares every block before it with
            # a typed turn in the same channel.
            # Each block goes in as a message, not a template, so its braces are text.
            blocks = [
                formatting_prompt,
                agent_static_prompt,
                tool_rules_prompt,
                skills_prompt,
                subgraphs_prompt,
                team_prompt,
                knowledge_prompt,
                agent_memory_prompt,
                live_context_prompt,
                channel_prompt,
                open_passes_prompt,
                voice_mode_prompt,
            ]

            # Filter out empty prompt messages (e.g. when channel_prompt is empty)
            system_messages = [SystemMessage(content=block) for block in blocks if block.strip()]
            return system_messages + convert_to_messages(state.get("messages", []))
        return dynamic_prompt

    async def build_graph(self, ctx, config):
        if config is None:
            raise ValueError(f"Agent configuration not found for: {getattr(ctx, 'agent_id', None)}")

        agent_id = ctx.agent_id
        agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agents"))
        agent_path = os.path.join(agents_dir, agent_id)

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
                if job_id and JobManager().honor_kill(job_id):
                    interrupt("Job was killed")

            # Every result passes through here before it becomes a ToolMessage,
            # so this is the one place a runaway output can be stopped for all
            # tools at once. See Config.tool_output_max_chars.
            def _cap(result):
                capped = cap_tool_output(result, Config().tool_output_max_chars)
                if capped is not result:
                    print(f"[GraphBuilder] Truncated '{t.name}' output from {len(result)} to ~{len(capped)} chars.")
                return capped

            @functools.wraps(original_run)
            def wrapper(*args, **kwargs):
                _abort_if_killed()
                return _cap(original_run(*args, **kwargs))
            
            t._run = wrapper
            
            if original_arun is not None:
                @functools.wraps(original_arun)
                async def awrapper(*args, **kwargs):
                    _abort_if_killed()
                    return _cap(await original_arun(*args, **kwargs))
                t._arun = awrapper
                
            return t

        allowed_tools = [make_interruptible(t) for t in allowed_tools]

        llm = chat_model(ctx, config)
        checkpointer = SqliteCheckpointer()

        prompt = self._get_prompt_template(ctx)

        # A live voice turn's first call may go to a light model: that's
        # graphs/voice, graphs/main plus the light call.
        light = light_call(ctx, config)
        graph_name = VOICE_GRAPH if light else config.get("graph", "main")
        graphs_loader = GraphsLoader()
        graph_info = graphs_loader.get_graph(graph_name)
        if light and not (graph_info and graph_info.get("create_graph")):
            print(f"[GraphBuilder] graphs/{VOICE_GRAPH} isn't there: {agent_id}'s voice turns run without the "
                  f"light call", file=sys.stderr)
            light, graph_name = None, config.get("graph", "main")
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
            config=config,
            **({"light": light} if light else {}),
        )
        # print(f"New Graph '{graph_name}' for {agent_id} built")
        return graph
