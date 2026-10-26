"""The events a turn streams, and what it leaves behind for its caller.

`Agent.execute_stream` yields dicts whose "type" is one of the names below.
Producers (streaming, agent, delegation, model/retry) and consumers
(the Discord runner, voice's reply, scripts/voice_dryrun) all import these
names, so a renamed event fails at import rather than silently in production.

Stream events, and their fields besides "type":
  TOKEN            content. Relayed from a delegated agent, also agent_id,
                   is_header (the "🌼 Daisy: " attribution) and is_error (its failure).
                   Written by a graph node itself, also agent_id.
  TOOL_START       tool_name, tool_args, run_id
  TOOL_END         tool_name, output, run_id
  REACTION         agent_id, emoji: an acknowledgement for the user's message
  HANDOFF          agent_id, name, emoji, channel, pointer, request: the message
                   named another agent (graphs/main), so what streams after this
                   is that agent's reply, for `channel`. `pointer` is all the
                   channel the message came from shows; `request` is the user's
                   words, for the channel the reply goes to.
  SUBAGENT_FINAL   agent_id, response, text: a delegated agent's parsed reply
  ERROR            content: the formatted error. The turn ends here.
  FINAL_RESPONSE   text, response (the AgentResponse), poll_data, image_paths,
                   video_paths, system_memory_log. The last event of a turn that
                   succeeded.

Custom events are dispatched inside the graph with `adispatch_custom_event`;
core/runtime/streaming.py turns them into the stream events above:
  SUBAGENT_STREAM_TOKEN   a delegated agent's token -> TOKEN
  SUBAGENT_STREAM_FINAL   a delegated agent's reply -> SUBAGENT_FINAL
  NODE_STREAM_TOKEN       text a graph node writes itself, with no model behind it
                          (graphs/main's fixed line) -> TOKEN
  ROUTE_REACTION          the turn was handed to another agent (delegation) -> REACTION
  ROUTE_HANDOFF           the message named another agent (graphs/main) -> HANDOFF
  LLM_RETRY_REACTION      a Gemini 503 was retried (model/retry) -> REACTION
"""

TOKEN = "token"
TOOL_START = "tool_start"
TOOL_END = "tool_end"
REACTION = "reaction"
HANDOFF = "handoff"
SUBAGENT_FINAL = "subagent_final"
ERROR = "error"
FINAL_RESPONSE = "final_response"

SUBAGENT_STREAM_TOKEN = "subagent_stream_token"
SUBAGENT_STREAM_FINAL = "subagent_stream_final"

# Emitted by a graph node that answers without a model (graphs/main's `fixed`
# node). Its reply reaches the channel from the final state either way, but
# the voice speaks only what streams, so a node's own text has to stream too.
NODE_STREAM_TOKEN = "node_stream_token"

# Emitted by `core.runtime.delegation` when a turn is handed to another agent.
#
# The acknowledgement emoji used to be driven by `ReactionCallbackHandler`
# watching `on_tool_start` for the name `agent_call`. That only works when a tool
# is what performed the delegation; the deterministic router delegates without
# calling a tool, and the emoji silently disappeared. Announcing it from the
# delegation path itself makes the signal independent of who decided to delegate.
ROUTE_REACTION = "route_reaction"

# Emitted by graphs/main's hand-off node before the named agent's reply streams:
# the readers (the Discord runner, voice's reply) end the reply in the channel
# the message came from with the pointer, and show what follows in the named
# agent's channel. A reader that doesn't know it shows everything in place.
ROUTE_HANDOFF = "route_handoff"

# Emitted by `core.runtime.model.retry` each time a Gemini 503 is retried, so the
# channel can show that a retry happened even though the error itself is hidden.
LLM_RETRY_REACTION = "llm_retry"

# Where `Agent.execute` leaves the parsed AgentResponse in `ctx.turn_hints`, for
# callers that need more than its text: delegation's stateless path, and the
# schedule runner's speech version.
RESPONSE_HINT = "agent_response"
