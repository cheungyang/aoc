"""The side work of a turn, as hooks: Agent runs the turn, the hooks do the rest.

  before(turn)          the turn is about to run: its graph is built
  after(turn)           it ran. `turn.response` is the agent's own parsed reply,
                        or None when a delegated agent's reply came back instead
  failed(turn, error)   the run raised
  ended(turn)           however it ended: done, failed, or the stream closed
                        early (a voice barge-in). Not async: it can run while a
                        stream is being closed, where nothing can be awaited.

The built-in hooks, which bootstrap.wire() registers for every agent:

  PruneHook       the history prune: the next turn of a session waits for the
                  previous one's; each turn's runs after it, in the background
  JobHook         the job: registered, running, then completed, partial or error
  MemoryLogHook   the reply's <system_memory_log>, saved to the agent's memory log

A runtime nobody wired fails loudly, naming bootstrap.wire(), as the other
ports do.
"""
import asyncio
import contextvars
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from core.channel.response_parser import AgentResponse
from core.knowledge.memory.logs import save_agent_memory_log
from core.runtime.foundation.context import ExecutionContext, current_execution_context
from core.runtime.foundation.jobs import JobManager
from core.runtime.history.pruner import ContextPruner
from core.runtime.observability.turn_trace import current_turn_trace, detach_turn_trace


@dataclass
class Turn:
    """One turn of one agent, as the hooks see it."""
    agent_id: str
    agent_config: Dict[str, Any]
    ctx: ExecutionContext
    prompt: Any
    graph: Any = None
    config: Optional[Dict[str, Any]] = None  # the run's LangChain config
    response: Optional[AgentResponse] = None


class TurnHook:
    """Every hook point, doing nothing. Subclass and override what you need."""

    async def before(self, turn: Turn) -> None:
        pass

    async def after(self, turn: Turn) -> None:
        pass

    async def failed(self, turn: Turn, error: Exception) -> None:
        pass

    def ended(self, turn: Turn) -> None:
        pass


_hooks: Optional[List[TurnHook]] = None


def set_turn_hooks(hooks: Optional[Sequence[TurnHook]]) -> None:
    """Plugs in the hooks every turn runs (core/runtime/bootstrap.py)."""
    global _hooks
    _hooks = list(hooks) if hooks is not None else None


def turn_hooks() -> List[TurnHook]:
    if _hooks is None:
        raise RuntimeError("No turn hooks are plugged in: call core.runtime.bootstrap.wire() at startup.")
    return _hooks


class PruneHook(TurnHook):
    """History is pruned after each turn, not before: a summary call before the
    model's first token was audible on voice. The session's next turn waits for
    it, so it never reads a checkpoint the prune is still rewriting."""

    def __init__(self):
        # Keyed by session id so the session's next turn can wait for it.
        self._tasks: Dict[str, asyncio.Task] = {}

    async def before(self, turn: Turn) -> None:
        """Waits for the previous turn's prune of this session, if it is still running.

        Shielded: cancelling the new turn (a barge-in) must not abandon a
        checkpoint rewrite halfway.
        """
        pending = self._tasks.get(turn.ctx.session_id)
        if pending is None or pending.done():
            return
        if pending.get_loop() is not asyncio.get_running_loop():
            # Started on another of the process's event loops: it can't be
            # awaited from here. Same-session turns on two loops are rare enough
            # that proceeding beats failing the turn.
            return
        started = time.monotonic()
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # _prune_after_turn already logged it
        finally:
            trace = current_turn_trace()
            if trace is not None:
                trace.event("prune_wait", ms=round((time.monotonic() - started) * 1000))

    def ended(self, turn: Turn) -> None:
        """Prunes the session's history after the turn, in the background.

        Pruning summarises old messages with a model call once history passes its
        thresholds. Run before a turn, that call sat between the user and the
        first token; run after, it overlaps the time the user spends reading or
        listening. The turn that crosses the threshold runs over budget once.
        """
        session = turn.ctx
        if session.is_stateless():
            return
        pending = self._tasks.get(session.session_id)
        if pending is not None and not pending.done():
            return
        try:
            context = contextvars.copy_context()
            # Run as the session being pruned, wherever this is called from, and
            # outside the voice turn's trace: the summariser's model calls are not
            # part of the turn being measured.
            context.run(current_execution_context.set, session)
            context.run(detach_turn_trace)
            task = asyncio.get_running_loop().create_task(self._prune_after_turn(turn), context=context)
        except RuntimeError:
            return  # no running loop (a generator closed during shutdown): skip this time
        session_id = session.session_id
        self._tasks[session_id] = task

        def _forget(done: asyncio.Task) -> None:
            if self._tasks.get(session_id) is done:
                del self._tasks[session_id]

        task.add_done_callback(_forget)

    async def _prune_after_turn(self, turn: Turn) -> None:
        try:
            await ContextPruner(
                provider=turn.agent_config.get("provider")
            ).aauto_prune_session(session=turn.ctx)
        except Exception as e:
            print(f"[Agent:{turn.agent_id}] Warning: auto_prune_session failed for session "
                  f"'{turn.ctx.session_id}': {e}", file=sys.stderr)


class JobHook(TurnHook):
    """The turn's job: what `!jobs`, `job_status` and `job_kill` see."""

    async def before(self, turn: Turn) -> None:
        prompt = turn.prompt if isinstance(turn.prompt, str) else str(turn.prompt)
        # add_job inserts the job as "queued"; the transition to "running" has to happen after it,
        # otherwise the insert clobbers the status back to "queued" for the whole run.
        JobManager().add_job(session=turn.ctx, prompt=prompt)
        if turn.ctx.job_id:
            JobManager().update_job(turn.ctx.job_id, "running")

    async def after(self, turn: Turn) -> None:
        """Updates job status based on final graph state."""
        job_id = turn.ctx.job_id
        if not job_id:
            return

        if JobManager().is_killed(job_id):
            return

        graph = turn.graph
        state = graph.get_state(turn.config) if (graph is not None and hasattr(graph, "get_state")) else None
        if state and getattr(state, "next", None):
            JobManager().update_job(job_id, "partial")
        else:
            JobManager().update_job(job_id, "completed")

    async def failed(self, turn: Turn, error: Exception) -> None:
        JobManager().update_job(turn.ctx.job_id, "error")


class MemoryLogHook(TurnHook):
    """Saves the reply's in-band memory log (core/knowledge/memory/logs.py)."""

    async def after(self, turn: Turn) -> None:
        response = turn.response
        if response is None or not response.system_memory_log or not self._records_memory(turn):
            return
        save_agent_memory_log(turn.agent_id, response.system_memory_log)

    @staticmethod
    def _records_memory(turn: Turn) -> bool:
        """Stateless agents keep nothing between runs, so their logs would never
        be consumed; a non-recording call (a dream) must not log about itself."""
        if turn.agent_config.get("stateless"):
            return False
        return turn.ctx is None or turn.ctx.record_memory
