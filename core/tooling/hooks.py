"""Tool hooks: code outside the tools that wants to know what they did.

Every finished tool call, of any tool (tools/ and each agent's own) in any
agent's turn, is announced once to the hooks set here, as a `ToolCall`: the
tool, its arguments, its status, and the session it ran in. The transcript's
callback handler announces it (core/runtime/observability/transcript.py),
since every call passes through it. A tool does nothing to be covered.

    class Watcher(ToolHook):
        def called(self, call: ToolCall) -> None:
            ...

    set_tool_hooks(list(tool_hooks()) + [Watcher()])

A hook runs inline, between the call's reply and the model's next step, so it
must be quick: note what it needs, and do the work later. One that raises is
logged and skipped; the turn goes on.

Today the proactive team is the one hook (core/proactive/taps.py). What a call
means to it lives in its own table (core/proactive/effects.py), not in the tools.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Sequence, Tuple


@dataclass(frozen=True)
class ToolCall:
    """One finished tool call."""
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    # "ok", "partial" or "failed": the envelope's <status>, or "failed" when the
    # tool raised (transcript.tool_status).
    status: str = "ok"
    # The ExecutionContext the call ran in: its turn_hints are the turn's scratch space.
    session: Any = None


class ToolHook:
    """Told about every finished tool call. Subclass and override `called`."""

    def called(self, call: ToolCall) -> None:
        pass


_hooks: Tuple[ToolHook, ...] = ()


def set_tool_hooks(hooks: Sequence[ToolHook]) -> None:
    """Replaces the hooks every tool call is announced to."""
    global _hooks
    _hooks = tuple(hooks or ())


def tool_hooks() -> Tuple[ToolHook, ...]:
    return _hooks


def tool_called(call: ToolCall) -> None:
    """Announces `call` to every hook. A hook that raises is logged, never the call's problem."""
    for hook in _hooks:
        try:
            hook.called(call)
        except Exception as e:
            print(f"[tooling] {type(hook).__name__} missed {call.tool}: {type(e).__name__}: {e}")
