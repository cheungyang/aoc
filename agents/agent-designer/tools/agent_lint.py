"""agent_lint: agent-designer's static audit of an agent definition. Read-only.

A thin front end: the checks live in lib/lint.py, and WORKFLOW.md states them
rule by rule.
"""
import os

from core.tooling import Report, ToolError, agent_tool


@agent_tool
def agent_lint(report: Report, agent_id: str = "") -> str:
    """Audit an agent's definition for what will fail or cost model round trips. Read-only.
    Call it at the start of a round-trip audit and after writing or changing an agent's files.

    Reads: agents/, skills/, tools/ and graphs/, each audited agent's RULES.md in pkm/agents/,
        the voice catalog in core/voice/foundation/settings.py and the routing table in
        tests/core/runtime/foundation/test_routing.py.
    Computes: findings by kind, each citing a file and line: skills, tools, actions and paths
        its text names that are missing or not granted; grants that resolve to nothing, repeat
        or spell out a bundle, and malformed `!` deny keys; channels the concierge doesn't host
        or the routing table pins wrong; a voice missing, unknown or another agent's, with the
        free ones; tools without a WORKFLOW.md contract or a read grant for what they read;
        RULES.md keys that don't match lib/rules.py; files the prompt never loads, and persona
        files out of shape. Then its budgets (persona, preloads, prompt, tool schemas), tools,
        skills and schedules.
    Leaves out: whether a finding matters. Findings come from pattern matching: check the
        cited line before changing anything.

    Args:
        agent_id: The agent's folder under agents/, e.g. 'meal-planner'. Empty audits every
            agent, which is long; prefer one.
    """
    from agents.agent_designer.lib.lint import PROJECT_ROOT, lint_agents, render

    ids = [agent_id.strip()] if agent_id and agent_id.strip() else None
    try:
        reports = lint_agents(ids)
    except LookupError as e:
        raise ToolError(str(e)) from None
    for audited in reports:
        report.read(os.path.join(PROJECT_ROOT, "agents", audited.agent_id, "agent.json"))
    return render(reports)
