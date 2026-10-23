"""The events a turn streams, and what it leaves behind for its caller.

`Agent.execute_stream` yields dicts whose "type" is one of the names below.
Producers (streaming, agent, delegation, model/retry) and consumers
(the Discord runner, voice's reply, scripts/voice_dryrun) all import these
names, so a renamed event fails at import rather than silently in production.

Stream events, and their fields besides "type":
  TOKEN            content. Relayed from a delegated agent, also agent_id,
                   is_header (the "🌼 Daisy: " attribution) and is_error (its failure).
  TOOL_START       tool_name, tool_args, run_id
  TOOL_END         tool_name, output, run_id
  REACTION         agent_id, emoji: an acknowledgement for the user's message
  SUBAGENT_FINAL   agent_id, response, text: a delegated agent's parsed reply
  ERROR            content: the formatted error. The turn ends here.
  FINAL_RESPONSE   text, response (the AgentResponse), poll_data, image_paths,
                   video_paths, system_memory_log. The last event of a turn that
                   succeeded.

Custom events are dispatched inside the graph with `adispatch_custom_event`;
core/runtime/streaming.py turns them into the stream events above:
  SUBAGENT_STREAM_TOKEN   a delegated agent's token -> TOKEN
  SUBAGENT_STREAM_FINAL   a delegated agent's reply -> SUBAGENT_FINAL
  ROUTE_REACTION          the turn was handed to another agent (delegation) -> REACTION
  LLM_RETRY_REACTION      a Gemini 503 was retried (model/retry) -> REACTION
"""

TOKEN = "token"
TOOL_START = "tool_start"
TOOL_END = "tool_end"
REACTION = "reaction"
SUBAGENT_FINAL = "subagent_final"
ERROR = "error"
FINAL_RESPONSE = "final_response"

SUBAGENT_STREAM_TOKEN = "subagent_stream_token"
SUBAGENT_STREAM_FINAL = "subagent_stream_final"

# Emitted by `core.runtime.delegation` when a turn is handed to another agent.
#
# The acknowledgement emoji used to be driven by `ReactionCallbackHandler`
# watching `on_tool_start` for the name `agent_call`. That only works when a tool
# is what performed the delegation; the deterministic router delegates without
# calling a tool, and the emoji silently disappeared. Announcing it from the
# delegation path itself makes the signal independent of who decided to delegate.
ROUTE_REACTION = "route_reaction"

# Emitted by `core.runtime.model.retry` each time a Gemini 503 is retried, so the
# channel can show that a retry happened even though the error itself is hidden.
LLM_RETRY_REACTION = "llm_retry"

# Where `Agent.execute` leaves the parsed AgentResponse in `ctx.turn_hints`, for
# callers that need more than its text: delegation's stateless path, and the
# schedule runner's speech version.
RESPONSE_HINT = "agent_response"
