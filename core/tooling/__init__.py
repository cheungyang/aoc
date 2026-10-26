"""core.tooling: everything an agent tool needs from core/, in one place.

Agent tools (`agents/<agent_id>/tools/<name>.py`) and their scans import from
core/ only this package, so it is the one place to change if tools move
elsewhere (MCP, say). Shared tools in tools/ use it for their envelope,
`format_tool_response`, and import the rest of core/ directly.

    from core.tooling import (
        agent_tool, Report, DataProblem, ToolError,  # the decorator, the report, failures
        Rule, load_rules,                            # thresholds: lib/rules.py over RULES.md
        has_permission, require_permission,          # checks on what the tool writes
        pkm_path, display_path,                      # the user's PKM
        get_local_now, current_agent_id,
    )

- agent_tool.py: `@agent_tool` (its docstring is the contract, with an
  example), the permission checks, `ToolError`.
- report.py: `Report`, `DataProblem` and the envelope: status, issues, explain;
  `format_tool_response`, the shared tools' envelope.
- rules.py: `Rule`, `load_rules`, RULES.md's format.
- paths.py: `pkm_path`, `display_path`.
- workflow.py: WORKFLOW.md, the contract each agent's tools keep: its format,
  the example runner and the contract checks the test suite runs. Not for tools.
"""
from core.tooling.agent_tool import (
    AgentTool,
    PermissionDenied,
    ToolError,
    agent_tool,
    current_agent_id,
    has_permission,
    require_permission,
    tool_grant,
)
from core.tooling.paths import display_path, pkm_path
from core.tooling.report import FAILED, OK, PARTIAL, DataProblem, Issue, Report, format_tool_response
from core.tooling.rules import Rule, load_rules, render_rules_file, rules_path
from core.util.local_time import get_local_now, get_local_timezone

__all__ = [
    "AgentTool",
    "DataProblem",
    "FAILED",
    "Issue",
    "OK",
    "PARTIAL",
    "PermissionDenied",
    "Report",
    "Rule",
    "ToolError",
    "agent_tool",
    "current_agent_id",
    "display_path",
    "format_tool_response",
    "get_local_now",
    "get_local_timezone",
    "has_permission",
    "load_rules",
    "pkm_path",
    "render_rules_file",
    "require_permission",
    "rules_path",
    "tool_grant",
]
