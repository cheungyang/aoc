"""The runtime: how one agent's turn runs, and what it needs around it.

  the turn, at the root:
    agent.py             Agent: prepares, runs and finishes one turn
    base_agent.py        BaseAgent: what every agent implements
    script_executor.py   ScriptExecutorAgent: runs scripts, no model
    agent_factory.py     build_agent: which class an agent's config becomes
    graph_builder.py     GraphBuilder: the agent's compiled LangGraph graph
    streaming.py         LangGraph's events turned into the turn's events
  delegation.py          one agent's turn handed to another
  bootstrap.py           plugs the runtime into the packages below it, at startup
  foundation/            what everything shares: context, sessions, jobs, events, routing
  model/                 the model call: thinking level, retry
  history/               a session's history across turns
  observability/         the transcript, token usage, latency marks
  commands/              every command, typed or spoken: !new, !help, !join, "stop",
                         "switch me to Daisy", !claim...; the adapters run them

Layers, lowest first. A module imports from its own layer or the ones below
it, never above (checked by tests/test_layers.py):

  foundation   foundation/: imports nothing else from the runtime
  services     commands/, model/, history/, observability/
  turn         agent, base_agent, script_executor, agent_factory, graph_builder, streaming
  delegation   delegation
  bootstrap    bootstrap

Rules:
  1. The runtime never imports an adapter (core.channel.discord, core.voice,
     core.scheduler, graphs, tools, scripts). Adapters import the runtime.
  2. Inside the runtime, imports point down the layers above.
  3. The packages the runtime builds on (core.prompts, loaders, storage,
     knowledge, tooling, permissions, channel, util, config), and core.git,
     import nothing above them: no adapter, and from the runtime only
     core.runtime.foundation. When one of them has to call up, it declares a
     port and bootstrap fills it. config, util and git are stricter still:
     see their __init__.py.
  4. A package __init__.py is a docstring: no imports, no side effects.
  5. An import inside a function is for an optional heavy dependency, never to
     dodge a cycle.
"""
