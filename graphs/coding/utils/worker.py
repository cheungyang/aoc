"""The one place the coding graph reaches the worker.

Both nodes that call `graph-worker` go through here, because the call needs
something neither of them can see on its own: the graph binding.

A worker's tool roster is the merge of its own `agent.json` — which grants it
nothing — and the grants of the graph it is running under. That binding travels
in the ambient execution context, and under the scheduled tick there was no
context for it to travel in: `coding_tick.py` invokes the compiled graph
directly, so `agent_call` found no caller to inherit from and handed the worker
`graph_id=None`. The worker got `['filesystem', 'load_skill']` scoped to its own
PKM folder instead of `['bash', 'filesystem']` scoped to the worktree, and every
call it made came back:

    Error: Agent graph-worker does not have permission to perform 'ls' on
    path /Users/alvac/aoc/workspaces/runs/run_21B6

It reported `FAILED`, changed nothing, and the tick recorded that as an `llm`
failure — so a permission problem no model can fix spent the entire implement
budget, three ticks in a row, on all three tasks.

The same binding is what `preflight` checks, so both go through `worker_session`
here: a preflight that builds its own context can pass while the real call
fails, which is exactly what happened (the log shows the two rosters one line
apart).
"""
from typing import Any, Optional

WORKER_AGENT_ID = "graph-worker"

# Identity for a tick with no human behind it. It only names the caller in the
# worker's prompt; the authority comes from `graph_id`.
TICK_CALLER_ID = "coding-tick"


def worker_session(graph_id: str, channel: Optional[Any] = None, source: str = "tool"):
    """Builds the context the worker runs under — the only supported way.

    `graph_id` is the authority field: it decides which tools and skills get
    merged into the worker's roster.
    """
    from core.runtime.session_manager import SessionManager

    return SessionManager.get_session(
        agent_id=WORKER_AGENT_ID,
        source=source,
        channel=channel,
        stateless=True,
        graph_id=graph_id,
    )


def _bind_graph(graph_id: str):
    """Puts this graph's binding where `agent_call` will look for it.

    Returns a contextvar token to reset, or None when the ambient context is
    already bound to this graph (the interactive path, where `graph_call` has
    already done it).
    """
    from core.runtime.execution_context import current_execution_context, try_context
    from core.runtime.session_manager import SessionManager

    ambient = try_context()
    if ambient is not None and ambient.graph_id == graph_id:
        return None

    if ambient is not None:
        bound = ambient.with_graph(graph_id)
    else:
        # The scheduled tick: no caller, so the graph speaks for itself.
        bound = SessionManager.get_session(
            agent_id=TICK_CALLER_ID, source="job", graph_id=graph_id
        )

    return current_execution_context.set(bound)


async def call_worker(prompt: str, graph_id: str, channel: str) -> str:
    """Runs `graph-worker` under `graph_id`'s grants and returns its raw reply."""
    from core.runtime.execution_context import current_execution_context
    from tools.agent_call import agent_call

    token = _bind_graph(graph_id or "coding")
    try:
        result = await agent_call.ainvoke({
            "agent_id": WORKER_AGENT_ID,
            "prompt": prompt,
            "channel": channel or "coding-pipeline",
        })
        return str(result)
    finally:
        if token is not None:
            current_execution_context.reset(token)
