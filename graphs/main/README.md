# Main Graph

`graphs/main` is the default LangGraph workflow for **every** agent in the
system: [`GraphBuilder.build_graph`](../../core/runtime/graph_builder.py#L194-L209)
reads `config.get("graph", "main")` and no `agent.json` currently overrides it.
The graph puts a deterministic router in front of the conversational ReAct
agent. A turn whose answer is already fixed by configuration — a channel in
which exactly one specialist is allowed to answer — is delegated without any
model call. A message in a channel no single agent answers (`#general`) gets a
fixed line, also without a model call. Every other turn — the executing
agent's own — reaches the concierge (the ReAct agent) as before. It runs on
every message an agent handles: Discord messages, voice turns, scheduled jobs,
`agent_call` delegations, and `graph_call` invocations all execute through the
compiled graph returned by [create_graph](graph.py#L72-L193).

> [!IMPORTANT]
> Because this graph is shared by all agents, the router must stay **inert**
> for specialists. Any change here has to keep an agent that does not host
> the channel, or lists it in its own `channels`, or is running a turn
> addressed to it (a delegation, job or schedule), on the concierge path — see
> [routing_enabled](../../core/runtime/foundation/routing.py#L62-L85).

## Workflow

```mermaid
flowchart TD
    START([START]) --> route["route (_decide)"]
    route -->|"state['route'] == 'direct_call'"| direct_call["direct_call (_direct_call)"]
    route -->|"state['route'] == 'fixed'"| fixed["fixed (_fixed)"]
    route -->|"state['route'] == 'concierge' (default)"| concierge["concierge (_concierge)"]
    direct_call --> END([END])
    fixed --> END
    concierge --> END
```

The conditional edge is
`lambda state: state.get("route") or CONCIERGE`, mapped to
`{DIRECT_CALL: DIRECT_CALL, FIXED: FIXED, CONCIERGE: CONCIERGE}`
([graph.py:L184-L188](graph.py#L184-L188)),
so a missing or empty `route` falls back to `concierge`.

The `concierge` node wraps an inner `create_react_agent(llm, tools, prompt=prompt)`
which runs its own model/tool loop internally; that loop is not expressed as
nodes of this graph.

## State

The state schema is `RouterState`, a subclass of `MessagesState`
([graph.py:L172-L175](graph.py#L172-L175)).

| Field | Type | Description |
| --- | --- | --- |
| `messages` | `list[BaseMessage]` (from `MessagesState`) | Conversation history. Nodes return new messages; the compiled graph persists them via the checkpointer. |
| `route` | `str` | Route target chosen by `route`: `"direct_call"`, `"fixed"` or `"concierge"`. Read by the conditional edge. |
| `route_agent_id` | `Optional[str]` | Target agent id when the route is `direct_call`; `None` otherwise. |
| `route_reason` | `str` | Human-readable rule that fired (e.g. `"sole eligible agent in #receipts"`). Kept for logs and tests, not consumed by any edge. |

## Nodes

### `route`

[_decide](graph.py#L79-L107) — resolves the
route and stashes it for the conditional edge. Synchronous and model-free.

- **Inputs from state:** `messages` (only the last message's `content` is
  read, as `latest`, and carried into the decision unchanged).
- **Other inputs:** the ambient
  [`ExecutionContext`](../../core/runtime/foundation/context.py#L94-L136)
  via `try_context()` (for `agent_id`, `channel_name` and `source`), the agent
  config captured at build time, a fresh
  [`AgentsLoader`](../../core/loaders/agents_loader.py#L99-L130)
  as the routing-table source, and
  [`Config().routing_llm_fallback`](../../core/config/settings.py#L150)
  (the rollback switch). When there is no active context it falls back to
  `owner_agent_id` (from `agent_id`/`config["agent_id"]`/`config["id"]`), an
  empty channel name and an empty source — which makes `routing_enabled`
  false, i.e. concierge.
- **Writes to state:** `route`, `route_agent_id`, `route_reason`. It never
  rewrites the message.
- **Side effects:** prints `[graphs.main] Routing to '<agent>' (<reason>)` when
  the decision is direct, and `[graphs.main] Fixed line (<reason>)` when it is
  fixed.
- **Routes to:** `direct_call`, `fixed` or `concierge`.

### `direct_call`

[_direct_call](graph.py#L109-L142) — hands
the turn to the one agent allowed to answer in this channel. Async.

- **Inputs from state:** `messages[-1].content` as the user prompt,
  `route_agent_id` as the delegation target.
- **Other inputs:** the ambient context for `channel_name` and the calling
  `agent_id` (falling back to `owner_agent_id`).
- **Behavior:** awaits
  [stream_delegate](../../core/runtime/delegation.py#L115),
  the single shared delegation path (also used by the `agent_call` tool), so
  the streamed header, custom events, channel-permission check and `<caller>`
  prefix are identical on both paths.
  - On failure it does **not** raise: it logs to stderr and returns
    `AIMessage("Could not reach \`<target>\`: <error>")`, because a
    misconfigured channel should say so in the channel rather than crash the
    graph ([graph.py:L125-L129](graph.py#L125-L129)).
  - On success the reply text is prefixed with
    [agent_header](../../core/runtime/delegation.py#L108-L112)
    (`emoji Name: `). Attribution matters: this text becomes part of the
    orchestrator's own history, and an unattributed reply would later read as
    something the orchestrator itself said and reasoned its way to.
  - When a context is active it calls
    [_record_turn](graph.py#L40-L58), which
    appends the `user` and `ai` messages to
    [`SessionStore`](../../core/storage/sessions.py). This is
    needed because the session log is populated by `LoggingHandler`'s *LLM*
    callbacks, and a routed turn never calls a model — without it the
    transcript would skip every deterministic exchange.
- **Writes to state:** `messages` — a single `AIMessage` with the attributed
  text (or `result.text`, or the error message).
- **Routes to:** `END`.

### `fixed`

[_fixed](graph.py#L144-L163) — answers a
message in a channel no single agent answers with a fixed line. Async; no
model runs.

- **Inputs from state:** `messages[-1].content`, for the session log only.
- **Other inputs:** the ambient context's surface
  ([`get_surface`](../../core/runtime/foundation/context.py#L243-L249)).
- **Behavior:** [fixed_reply](graph.py#L61-L69) picks the line:
  - typed: `UNADDRESSED_TEXT` — post in the agent's own channel, or type
    `!help` for commands. It never suggests addressing an agent by name in the
    message, since that would only bring the user back to this line;
  - spoken (the voice lobby): `<speech>Who's it for? Say 'switch to Daisy'.</speech>`
    — only a `<speech>` block, so the voice speaks it and the channel shows it
    as the whole reply.

  The line is dispatched as a
  [`NODE_STREAM_TOKEN`](../../core/runtime/foundation/events.py) custom event
  as well as returned: the channel would show it from the final state anyway,
  but the voice speaks only what streams. The node then calls `_record_turn`,
  for the same reason `direct_call` does.
- **Writes to state:** `messages` — a single `AIMessage` with the line.
- **Routes to:** `END`.

### `concierge`

[_concierge](graph.py#L165-L170) — the
ReAct behaviour, for turns that are the executing agent's own. Async.

- **Inputs from state:** `messages` (the full list), plus the LangGraph
  `config` passed through as `config_`.
- **Behavior:** awaits `concierge_agent.ainvoke({"messages": existing}, config_)`
  where `concierge_agent = create_react_agent(llm, tools, prompt=prompt)` was
  built once in `create_graph`, then slices off only the messages the inner
  agent produced (`result["messages"][len(existing):]`) so the pre-existing
  history is not re-emitted into the outer state.
- **Writes to state:** `messages` — only the newly produced messages.
- **Routes to:** `END`.

## Routing

All routing logic lives in [core/runtime/foundation/routing.py](../../core/runtime/foundation/routing.py),
where voice and channel posts use it too, and is deliberately pure: `decide()` is a function of
`(prompt, agent_id, channel_name, agent_config, loader, source, llm_fallback)` and performs no work
and no network I/O, so the routing table is something a test can pin.

Routing is **deterministic only**. Guessing the agent for a message in
`#general` with the Concierge's model was retired: nearly every message is
typed in its agent's own channel, and `#general` is used for commands. The job
verbs that used to be routed to the Concierge's model (`jobs`, `kill …`) are
typed commands now: `!jobs`, `!job <id>`, `!kill <id>`
([core/runtime/commands/typed/](../../core/runtime/commands/typed/jobs.py)).
The Concierge holds no tools; `#content-creation` belongs to 🎬 Coco
(`content-creator`), who starts and resumes `content_creation` runs.

[decide](../../core/runtime/foundation/routing.py#L88-L114) returns a
frozen [RouteDecision](../../core/runtime/foundation/routing.py#L44-L59)
(`target`, `agent_id`, `prompt`, `reason`; `is_direct` is a convenience
property). `reason` exists for logs and tests: when routing is wrong, the
useful question is *which rule fired*.

### Decision table (evaluated top to bottom)

| Condition | Route | `agent_id` | `reason` |
| --- | --- | --- | --- |
| `routing_enabled(...)` is false | `concierge` | `None` | `routing not enabled for this agent/channel` |
| Exactly 1 eligible agent in the channel | `direct_call` | that agent | `sole eligible agent in #<channel>` |
| 0 or ≥2 eligible agents, `ROUTING_LLM_FALLBACK` on | `concierge` | `None` | `<n> eligible agents in #<channel>; ROUTING_LLM_FALLBACK is on` |
| 0 or ≥2 eligible agents | `fixed` | `None` | `<n> eligible agents in #<channel>` |

### `routing_enabled`

[routing_enabled](../../core/runtime/foundation/routing.py#L62-L85)
requires all of:

1. the turn's `source` is not in `ADDRESSED_SOURCES` (`tool`, `job`,
   `scheduled`, `graph`): a delegation, a job, a scheduled run or a graph's
   worker is addressed to the agent running it, whatever channel it names —
   the dream standup reaches the Concierge by delegation, in `#general`, and
   must reach its model, not the fixed line. A message typed or spoken in a
   channel (`discord`; a voice turn runs on its channel's `discord` session),
   or a replay of one (`voice_replay`, scripts/voice_dryrun.py), is routed;
2. a non-empty `agent_id` **and** non-empty `channel_name`;
3. the channel is **not** in the agent's own `channels` — an agent that answers
   in a channel it hosts has nobody to hand it to;
4. `loader.hosts_channel(agent_id, channel_name)` — i.e. the channel is listed
   in the agent's `channel_hosts`
   ([agents_loader.py:L99-L107](../../core/loaders/agents_loader.py#L99-L107)).

This is *derived rather than declared* on purpose: because `graphs/main` is the
default graph for every agent, a flag in one agent's config would not have
scoped it and a flag in every agent's config would be a migration. Only the
Concierge satisfies all four today: it hosts every channel and lists none
(its `channels` is `["*"]`).

### Resolving the target agent

When routing is enabled, the target comes from
[eligible_agents_for_channel(channel_name, exclude_agent_id=agent_id)](../../core/loaders/agents_loader.py#L109-L137):
agents whose `channels` list contains the channel, **excluding** the caller
itself and **excluding** agents declaring `channels: ["*"]` (a wildcard is a
statement about reachability, not ownership — counting `graph-worker` would
make every channel look shared and give each the fixed line). The result is
sorted for stability. Exactly one survivor ⇒ `direct_call`; otherwise no single
agent owns the channel, and the message gets the fixed line.

> [!NOTE]
> `direct_call` delegation is still permission-checked downstream:
> [channel_permits](../../core/runtime/delegation.py#L102-L105)
> inside `stream_delegate` rejects a target whose `channels` grant does not
> cover the channel, and the node reports that as the turn's reply.

### The rollback switch

`ROUTING_LLM_FALLBACK=1` (a `Setting` in
[core/config/settings.py](../../core/config/settings.py#L150), off by default)
sends a message in a channel without one agent to the host's model instead of
the fixed line. It changes nothing else: a channel's one agent is still reached
without a model. The Concierge now holds no tools, so with only the flag on it
can talk but not delegate; the full rollback also restores its `agent.json`,
`AGENTS.md` and `SOUL.md`.

## Entry & Exit

Both helpers are picked up by name by
[GraphsLoader](../../core/loaders/graphs_loader.py#L80-L97)
and used by the [`graph_call`](../../tools/graph_call.py#L40-L42)
tool when this graph is invoked as a subgraph. (The normal agent path in
[`Agent._prepare_execution`](../../core/runtime/agent.py#L55)
builds `{"messages": [...]}` itself and does not call them.)

```python
def prepare_input(query: str, caller: Optional[str] = None, **kwargs) -> Dict[str, Any]
```

Returns `{"messages": [{"role": "user", "content": formatted_query}]}`. When
`caller` is given and the query does not already contain `<caller>`, the query
is prefixed with `<caller>{caller}</caller>\n`; the guard makes it idempotent
so a re-entering prompt is not double-tagged
([graph.py:L196-L204](graph.py#L196-L204)).

```python
def format_output(state: Dict[str, Any]) -> str
```

Returns `state["messages"][-1].content` when `state` is a dict with a non-empty
`messages`; otherwise falls back to `str(state)`
([graph.py:L207-L211](graph.py#L207-L211)).

### Checkpointer ownership

```python
concierge_agent = create_react_agent(llm, tools, prompt=prompt)   # no checkpointer
...
return workflow.compile(checkpointer=checkpointer)                # outer graph owns it
```

> [!IMPORTANT]
> The checkpointer belongs to this **outer** graph. The inner ReAct agent is
> compiled without one — otherwise the two would write the same thread from two
> levels. `GraphBuilder` passes a
> [`SqliteCheckpointer`](../../core/runtime/graph_builder.py#L190)
> in, and the thread id comes from
> [`ExecutionContext.get_session_thread_id()`](../../core/runtime/foundation/context.py#L351-L363).

A consequence of this design is that the `direct_call` and `fixed` nodes have
to write the human-readable session log themselves (`_record_turn`), since the
checkpoint is handled automatically by the compiled graph but `SessionStore` is
only populated by LLM callbacks that a routed turn never triggers.

## Files

| File | Description |
| --- | --- |
| [graph.py](graph.py) | Builds and compiles the workflow: the `route`, `direct_call`, `fixed` and `concierge` nodes, `RouterState`, the fixed lines, plus `prepare_input`/`format_output`. |
| [graph.json](graph.json) | Graph metadata consumed by `GraphsLoader`: `graph_id`/`name` `main`, description, emoji 🧭, no extra tool grants, `dream` skill. |

### Tests

| File | Description |
| --- | --- |
| [test_main_graph.py](../../tests/graphs/main/test_main_graph.py) | Runs the compiled graph with doubles: direct call without a model, reply attribution, the fixed line typed and spoken (streamed, logged, no model), the `ROUTING_LLM_FALLBACK` path, former prefixes delegated as plain words, a delegated turn reaching the concierge, turn recording, failed delegation, and that specialists never route. |
| [test_routing.py](../../tests/core/runtime/foundation/test_routing.py) | Unit-tests `decide()`, `routing_enabled()` and `answering_agent()`, and pins the live routing table against the real `agents/` configs: every hosted channel but `#general` has exactly one agent, `#general` gets the fixed line, and the Concierge holds no tools. |
| [test_streaming.py](../../tests/core/runtime/test_streaming.py) | Includes `NODE_STREAM_TOKEN` arriving as the owner's token. |
| [test_smoke.py](../../tests/agents/content_creator/test_smoke.py) | Coco drives a `content_creation` run through both gates on its own session's thread. |
