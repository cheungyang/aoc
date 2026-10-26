from typing import Literal

from langchain_core.tools import tool

from core.tooling import format_tool_response


@tool
def set_thinking(level: Literal["minimal", "low", "medium", "high"], reason: str) -> str:
    """Set how deeply you reason on your NEXT model call in this turn; the call after it
    goes back to the usual level. Call it in the same step as the tools whose results
    you'll reason about, never on its own (that costs a round trip). Pick the lowest
    level that will get it right; if a tool already computed the numbers, you don't
    need high.

    minimal: relay or acknowledge what's already in front of you ("Logged.", reading out a tool result).
    low: conversation, or one clear decision (answering from context, summarizing tool output).
    medium: several steps or constraints (planning tool calls; choosing among options with 2-4 constraints; structured edits).
    high: calculation no tool covers, many-constraint selection, synthesis, or an irreversible change.

    Voice turns cap at medium.

    Args:
        level: minimal, low, medium or high.
        reason: A few words on why the next step needs this depth; it is logged.
    """
    from core.runtime.model.thinking import pick_thinking

    try:
        applied = pick_thinking(level, reason)
    except (ValueError, RuntimeError) as e:
        return format_tool_response("set_thinking", payload="", errors=f"Error: {e}")
    note = "" if applied == level else f" (asked for {level}; voice turns cap at {applied})"
    return format_tool_response("set_thinking", payload=f"Next call: {applied}{note}")
