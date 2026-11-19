"""The reply to a tool call whose arguments the tool's schema refuses.

LangChain checks a call's arguments against the tool's schema before the tool
runs. A tool without `handle_validation_error` raises on a refusal: LangGraph
still hands the model an error, but the call ends without a reply, so the
session log gets no `Tool Output` row (core/runtime/observability/transcript.py).
The tool_failure detector (core/learning/detectors.py) then never counts the
failure, and pairs the turn's later replies with the wrong calls.

`give_reply(tool)` sets `reply` on a tool that has no way of its own. The graph
builder does it for every tool an agent gets (core/runtime/graph_builder.py),
so no tool needs to. tools/filesystem.py keeps its own reply, which also shows
the shape of an instruction.

The reply is the tool's usual envelope, failed: one `Error:` line per refused
argument, saying what it should be, then what to do. It names arguments and
never echoes a value: a value may be personal, and the first line becomes a
friction card's evidence, which Kaizen and #learning-lab see.

Not for tools: the runtime gives it to them.
"""
import functools
from typing import Any, Dict, List, Sequence, Tuple

from pydantic import ValidationError

from core.tooling.agent_tool import AgentTool
from core.tooling.report import Report, format_tool_response, render_envelope

AGAIN = "Nothing ran: call it again with the arguments fixed."


def give_reply(tool: Any) -> Any:
    """`tool`, now answering a refused call with `reply`, unless it has a way of its own."""
    if not getattr(tool, "handle_validation_error", None):
        tool.handle_validation_error = functools.partial(reply, tool.name, isinstance(tool, AgentTool))
    return tool


def reply(tool_name: str, agent_tool: bool, error: ValidationError) -> str:
    """The envelope for a refused call: an `@agent_tool`'s with status failed, or a shared tool's."""
    text = "\n".join(what_is_wrong(error.errors()) + [AGAIN])
    if agent_tool:
        return render_envelope(tool_name, Report(), errors=text)
    return format_tool_response(tool_name, payload="", errors=text)


def what_is_wrong(errors: Sequence[Dict[str, Any]]) -> List[str]:
    """One line per refusal, in the schema's order: the arguments missing from one object
    together, with the names that were sent (`Error: no "card". Sent: "cards", "days".`),
    and each bad value (`Error: bad "days": Input should be a valid integer.`)."""
    lines: List[str] = []
    missing: Dict[Tuple[Any, ...], Tuple[int, List[str], Any]] = {}
    for err in errors:
        loc = tuple(err.get("loc") or ())
        if err.get("type") == "missing" and loc:
            if loc[:-1] not in missing:
                missing[loc[:-1]] = (len(lines), [], err.get("input"))
                lines.append("")
            missing[loc[:-1]][1].append(name(loc))
        elif loc:
            lines.append(f'Error: bad "{name(loc)}": {sentence(err.get("msg"))}')
        else:
            lines.append(f"Error: {sentence(err.get('msg'))}")
    for at, names, given in missing.values():
        lines[at] = f"Error: no {either(names)}.{sent(given)}"
    return lines


def name(loc: Sequence[Any]) -> str:
    """An argument's place in the call: `days`, or `items[1].qty` inside a list."""
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def either(names: Sequence[str]) -> str:
    """`"a"`, `"a" or "b"`, `"a", "b" or "c"`."""
    quoted = [f'"{n}"' for n in names]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " or " + quoted[-1]


def sentence(msg: Any) -> str:
    text = str(msg or "invalid").strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def sent(given: Any) -> str:
    """` Sent: "a", "b".`: the names of the arguments that came, never their values."""
    if not isinstance(given, dict):
        return ""
    if not given:
        return " Sent: no arguments."
    return " Sent: " + ", ".join(f'"{key}"' for key in given) + "."
