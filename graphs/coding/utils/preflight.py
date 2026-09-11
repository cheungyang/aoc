"""Preflight checks that run before any LLM work.

Every check here answers the same question: *would this run be able to finish?*
Asking before the worker starts is the difference between one actionable line and
a full implementation run that dies at the push — the failure mode that produced
"Deterministic tester gate failed: pr_url is missing from state".
"""
from typing import Any, Dict, List, Optional, Tuple

from core.util import git_ops
from core.util.push_identity import PushIdentity
from graphs.coding.utils.repo import resolve_push_identity

WORKER_AGENT_ID = "graph-worker"


def check_required_tools(graph_id: str, required: Dict[str, List[str]]) -> Tuple[bool, str]:
    """Asserts each agent really receives the tools the graph promises it.

    The roster an agent gets is the merge of its own `agent.json` and the active
    graph's grants. When that merge goes wrong the worker does not error — it
    writes files it cannot test, or reports "completed without modifying files".
    Checking it up front turns that into one line naming the missing tool.
    """
    if not required:
        return True, ""

    from core.agent.session_manager import SessionManager
    from core.loaders.tools_loader import ToolsLoader

    loader = ToolsLoader()
    problems: List[str] = []

    for agent_id, tool_names in required.items():
        ctx = SessionManager.get_session(
            agent_id=agent_id, source="graph", stateless=True, graph_id=graph_id
        )
        try:
            available = {getattr(t, "name", "") for t in loader.get_tools(ctx)}
        except Exception as e:
            problems.append(f"could not resolve tools for `{agent_id}`: {e}")
            continue

        missing = [name for name in tool_names if name not in available]
        if missing:
            problems.append(
                f"`{agent_id}` is missing {', '.join(f'`{m}`' for m in missing)} "
                f"under graph `{graph_id}` (has: {', '.join(sorted(available)) or 'nothing'})"
            )

    if problems:
        return False, "Preflight failed: " + "; ".join(problems)
    return True, ""


async def preflight_tick(
    repo_descriptor: Dict[str, Any],
    graph_id: str = "coding",
    required_tools: Optional[Dict[str, List[str]]] = None,
    cwd: str = "."
) -> Tuple[bool, str, Optional[PushIdentity]]:
    """Runs every gate a tick depends on. Returns (ok, message, push_identity).

    Order matters: the local checks are free, so they run before anything that
    touches the network.
    """
    ok, message = check_required_tools(graph_id, required_tools or {})
    if not ok:
        return False, message, None

    push_identity, identity_error = resolve_push_identity(repo_descriptor)
    if identity_error:
        return False, f"Preflight failed: {identity_error}", None

    if push_identity:
        ok, message = await git_ops.preflight_push_access(
            target_repo=repo_descriptor.get("slug"),
            cwd=cwd,
            push_identity=push_identity
        )
        if not ok:
            return False, message, None

    return True, "", push_identity
