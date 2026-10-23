"""@agent_tool: the one decorator every agent tool uses (`agents/<agent_id>/tools/<name>.py`).

It does what each tool used to repeat, so the body holds only its logic:

1. Checks that the running agent may use the tool: its agent.json, or one of
   its skills, grants it. A call outside an agent run is refused.
2. Hands the body a fresh `Report`, its first parameter, to record what it
   read and what it couldn't.
3. Adds `explain: bool = False` to the tool's schema. With explain, the
   envelope also lists what was read and the inputs behind each number
   (`report.read`, `report.trace`); `report.explain` tells the body.
4. Renders the envelope (core/tooling/report.py) around what the body
   returns: a str as is, a dict or list as JSON.
5. Turns a failure into a `failed` envelope, never an exception that would
   end the turn:
   - `DataProblem` (from `report.raise_if_failed()`): required data is
     missing; the issues say what.
   - `ToolError("...")`: the call can't be done as asked (a bad argument, a
     duplicate, a refused write); the message goes to <errors>.
   - Any other exception: a bug; <errors> names it.

The result is a LangChain tool, like `@tool`'s. The tools loader binds
attribute `<name>` of `agents/<agent_id>/tools/<name>.py`, so the module, the
function and the tool share one name. The model sees the docstring (cleaned)
and the body's parameters without `report`, plus `explain`.

The docstring is in every prompt, so it stays short: what the tool is for,
then one line each starting `Reads:`, `Computes:` and `Leaves out:`, then
`Args:`. The rules in full, with worked examples, go in the agent's
WORKFLOW.md (core/tooling/workflow.py).

Example, a tool in `agents/<agent_id>/tools/credit_windows.py`:

    from core.tooling import Report, agent_tool, get_local_now, load_rules, pkm_path
    from agents.<package>.lib.credits import credit_windows as build
    from agents.<package>.lib.rules import RULES

    @agent_tool
    def credit_windows(report: Report, card: str = "") -> dict:
        \"\"\"Where every recurring statement credit stands today.

        Reads: pkm/wallet/credit-card/*.md, and RULES.md for the alert windows.
        Computes: each credit's period, days left and status; the "behind" and "lapsing" flags.
        Leaves out: which credit to use, and when.

        Args:
            card: Only this card's credits; empty for all.
        \"\"\"
        rules = load_rules("<agent_id>", RULES, report)
        windows = build(pkm_path("wallet", "credit-card"), get_local_now().date(), rules, report)
        report.raise_if_failed()  # no credit table at all: status failed, the issues say why
        return {"credits": [window.as_dict() for window in windows]}

A tool that writes checks its target as well: `require_permission("append",
path)` stops the call with a refusal the envelope reports, and
`has_permission(action, path)` asks without stopping. Its module also keeps
declaring `READ_ACTIONS` (`()` when every action writes), so the read-only
replay (scripts/voice_dryrun.py) refuses its writes.
"""
import contextvars
import functools
import inspect
import json
import typing
from typing import Annotated, Any, Callable, Optional

from langchain_core.tools import StructuredTool
from langgraph.errors import GraphBubbleUp
from pydantic import Field

from core.tooling.paths import display_path
from core.tooling.report import FAILED, DataProblem, Report, render_envelope

EXPLAIN_DESCRIPTION = (
    "Also list what was read (files, modified times, records) and the inputs behind each number. "
    "For checking a result."
)

# The tool whose body is running, for the permission checks below.
_running_tool = contextvars.ContextVar("core_tooling_running_tool", default=None)


class ToolError(Exception):
    """The call can't be done as asked. The message goes to <errors>, for the model to act on."""


class PermissionDenied(ToolError):
    """The agent's grants refuse an action (`require_permission`)."""


class AgentTool(StructuredTool):
    """A tool built by `@agent_tool`. Its own type, so tests can tell it from a plain `@tool`."""


def agent_tool(func: Callable[..., Any]) -> AgentTool:
    """Makes `func(report, ...)` an agent tool. See the module docstring."""
    name = func.__name__
    params = list(inspect.signature(func).parameters.values())
    if inspect.iscoroutinefunction(func):
        raise TypeError(f"@agent_tool {name}: write a plain function, not a coroutine")
    if not params or params[0].name != "report":
        raise TypeError(f"@agent_tool {name}: the first parameter must be `report: Report`")
    if "explain" in (p.name for p in params):
        raise TypeError(f"@agent_tool {name}: don't declare `explain`; the decorator adds it (read report.explain)")
    if any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params):
        raise TypeError(f"@agent_tool {name}: name every parameter; no *args or **kwargs")
    if not (func.__doc__ or "").strip():
        raise TypeError(f"@agent_tool {name}: needs a docstring; it is what the model reads")

    # The schema the model sees: the body's parameters without `report`, then `explain`.
    # Annotations are resolved here, against the tool's own module.
    hints = typing.get_type_hints(func, include_extras=True)
    visible = [p.replace(annotation=hints.get(p.name, p.annotation)) for p in params[1:]]
    visible.append(inspect.Parameter(
        "explain", inspect.Parameter.KEYWORD_ONLY, default=False,
        annotation=Annotated[bool, Field(description=EXPLAIN_DESCRIPTION)],
    ))

    def call(**kwargs: Any) -> str:
        report = Report(explain=bool(kwargs.pop("explain", False)))
        return _run(name, func, report, kwargs)

    functools.update_wrapper(call, func)
    call.__signature__ = inspect.Signature(visible, return_annotation=str)
    call.__annotations__ = {p.name: p.annotation for p in visible if p.annotation is not p.empty}
    call.__annotations__["return"] = str
    return AgentTool.from_function(func=call, name=name, description=inspect.cleandoc(func.__doc__))


def _run(name: str, func: Callable[..., Any], report: Report, kwargs: dict) -> str:
    from core.runtime.foundation.context import try_context

    ctx = try_context()
    if ctx is None:
        return render_envelope(
            name, report, errors="Error: no active execution context; this tool must be called from an agent run.")
    token = _running_tool.set(name)
    try:
        if not _loader().check_permission(ctx, name):
            return render_envelope(
                name, report, errors=f"Error: Agent '{ctx.agent_id}' does not have permission to use {name}.")
        payload = func(report, **kwargs)
    except GraphBubbleUp:
        raise  # LangGraph's own control flow (an interrupt), not a failure of the tool
    except DataProblem as e:
        report.merge(e.report)
        if report.status != FAILED:
            report.missing(name, str(e))
        return render_envelope(name, report)
    except ToolError as e:
        return render_envelope(name, report, errors=f"Error: {e}")
    except Exception as e:  # a bug: reported to the model, not raised into the turn
        return render_envelope(name, report, errors=f"Error in {name}: {type(e).__name__}: {e}")
    finally:
        _running_tool.reset(token)
    return render_envelope(name, report, payload=_as_text(payload))


def _as_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (dict, list, tuple)):
        return json.dumps(payload, ensure_ascii=False, default=str)
    return str(payload)


def _loader():
    # Looked up per call, so a patched ToolsLoader (the read-only replay) applies.
    from core.loaders.tools_loader import ToolsLoader

    return ToolsLoader()


def _running_call(caller: str):
    from core.runtime.foundation.context import try_context

    name, ctx = _running_tool.get(), try_context()
    if name is None or ctx is None:
        raise RuntimeError(f"{caller}() works only inside an @agent_tool call")
    return name, ctx


def has_permission(action: str, target: Optional[str] = None) -> bool:
    """Whether the running agent may do `action` on `target` with the tool being run.

    `target` is the absolute path of a file (`pkm_path(...)`), or whatever the
    tool's grants are keyed by. Grants come from agent.json and the agent's
    skills; an empty grant (`{}`) allows every action.
    """
    name, ctx = _running_call("has_permission")
    return bool(_loader().check_permission(ctx, name, action, target))


def require_permission(action: str, target: Optional[str] = None) -> None:
    """Stops the call with `PermissionDenied` unless `has_permission(action, target)`."""
    if not has_permission(action, target):
        where = f" {display_path(target)}" if target else ""
        raise PermissionDenied(f"Agent '{current_agent_id()}' does not have permission to '{action}'{where}.")


def tool_grant() -> Any:
    """The running agent's grant for the tool being run, as merged from agent.json and its skills.

    None when not granted; `{}` or `[]` grants every action; else the dict or
    list as written. For a tool that must tell an explicit grant from a blanket one.
    """
    name, ctx = _running_call("tool_grant")
    return _loader().get_tool_permissions(ctx, name)


def current_agent_id() -> str:
    """The id of the agent whose run this is, e.g. "meal-planner"."""
    from core.runtime.foundation.context import try_context

    ctx = try_context()
    if ctx is None:
        raise RuntimeError("current_agent_id() works only during an agent run")
    return ctx.agent_id
