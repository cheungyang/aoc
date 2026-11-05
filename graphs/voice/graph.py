"""The voice graph: graphs/main, with a voice turn's first model call on a light model.

GraphBuilder builds it instead of graphs/main for a live voice turn of the
agent you're talking to, while VOICE_LIGHT_MODEL names a light model
(core/runtime/graph_builder.py `light_call`). Everything is graphs/main's --
the route step, hand-offs, the checkpointer -- except the ReAct step's model,
which has two speeds:

  1. The turn's first call (the user's words are the last message) goes to
     the light model: the agent's whole prompt and history, no tools, the
     light model's own thinking level, and the light call's rules after the
     system blocks (prompts/light_call.md). It begins with <speech>. When the
     answer is already in the conversation it answers, and the turn ends.
  2. Otherwise it says in one sentence what the agent will do and ends with
     `<handover depth="low"/>`, which its model turns into a `handover` call
     as it streams (core/runtime/model/handover.py). A reply that's only that
     sentence, with the marker left off, hands over too (`opener_only`).
  3. The `handover` tool runs here: the depth goes to the agent's next call
     (`pick_thinking`, capped on voice), the voice block stops asking for a
     sentence before tool calls (core/prompts/voice.py `OPENER_HINT`), and
     it's logged and put on the voice trace. Its result tells the agent's
     model that the user heard the first line.
  4. Every later call of the turn runs on the agent's own model, with its
     tools. `handover` is never declared to a model: it's run here only.
"""
from typing import Any, List, Optional, Tuple

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.prompt_values import PromptValue
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool

from core.config.models import resolve_model
from core.config.settings import Config
from core.prompts.voice import OPENER_HINT
from core.runtime.foundation.context import try_context
from core.runtime.model.handover import DEFAULT_DEPTH, HANDOVER, MISSING, LightChatModel
from core.runtime.model.thinking import pick_thinking
from core.runtime.observability.turn_trace import current_turn_trace
from graphs.main.graph import create_graph as create_main_graph
from graphs.voice.prompts import HandedOverPrompt, LightCallPrompt


@tool(HANDOVER)
async def handover(depth: str = DEFAULT_DEPTH, marker: str = "found") -> str:
    """Hands a voice turn from its light first call to the agent's own model, at
    `depth` thinking. `marker` is "missing" when the light call said its line
    and left the marker off."""
    ctx = try_context()
    applied = pick_thinking(depth)
    agent = ctx.agent_id if ctx is not None else None
    if ctx is not None:
        ctx.turn_hints[OPENER_HINT] = True
    missing = f" marker={MISSING}" if marker == MISSING else ""
    print(f"[Handover] agent={agent} depth={depth} applied={applied}{missing}")
    trace = current_turn_trace()
    if trace is not None:
        trace.event("handover", agent=agent, depth=depth, applied=applied, marker=marker)
    # What the agent's model reads as the hand-over's result (prompts/handed_over.md).
    return HandedOverPrompt().render()


def first_call(state: Any) -> bool:
    """Whether the ReAct step is about to make the turn's first model call: the
    user's words are the last message (after it, a reply or a tool result is)."""
    messages = state.get("messages") if isinstance(state, dict) else getattr(state, "messages", None)
    return bool(messages) and isinstance(messages[-1], HumanMessage)


def with_light_rules(messages: Any) -> List[BaseMessage]:
    """The light call's input: the agent's prompt and history, with the light
    call's rules after the system blocks."""
    if isinstance(messages, PromptValue):
        messages = messages.to_messages()
    messages = list(messages)
    after = next((i for i, message in enumerate(messages) if not isinstance(message, SystemMessage)), len(messages))
    return [*messages[:after], SystemMessage(content=LightCallPrompt().render()), *messages[after:]]


def create_graph(llm, tools, prompt=None, checkpointer=None, agent_id=None, config=None,
                 light: Optional[Tuple[str, str]] = None, **kwargs):
    """graphs/main's graph, whose ReAct step makes a turn's first model call on
    `light` -- (tier, thinking level), from GraphBuilder -- with no tools, and
    every other on `llm` with `tools`. Without `light`, graphs/main's own."""
    if light is None:
        return create_main_graph(llm, tools, prompt=prompt, checkpointer=checkpointer, agent_id=agent_id,
                                 config=config, **kwargs)
    tier, level = light
    light_model = LightChatModel(model=resolve_model(tier), max_retries=Config().llm_max_retries)
    light_call = RunnableLambda(with_light_rules, name="light_rules") | light_model.bind(thinking_level=level)
    agent_model = llm.bind_tools(tools) if tools else llm

    def two_speed(state, runtime):
        return light_call if first_call(state) else agent_model

    return create_main_graph(llm, [*tools, handover], prompt=prompt, checkpointer=checkpointer, agent_id=agent_id,
                             config=config, model=two_speed, **kwargs)
