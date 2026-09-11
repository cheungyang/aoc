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


def check_required_tools(graph_id: str, tool_names: List[str]) -> Tuple[bool, str]:
    """Asserts the worker really receives the tools this graph grants it.

    The names come from the graph's own `tools` block — the grant *is* the
    requirement, so there is nothing to keep in sync.

    The roster an agent gets is the merge of its own `agent.json` and the active
    graph's grants. When that merge goes wrong the worker does not error — it
    writes files it cannot test, or reports "completed without modifying files".
    Checking it up front turns that into one line naming the missing tool.
    """
    if not tool_names:
        return True, ""

    from core.agent.session_manager import SessionManager
    from core.loaders.tools_loader import ToolsLoader

    loader = ToolsLoader()

    ctx = SessionManager.get_session(
        agent_id=WORKER_AGENT_ID, source="graph", stateless=True, graph_id=graph_id
    )
    try:
        available = {getattr(t, "name", "") for t in loader.get_tools(ctx)}
    except Exception as e:
        return False, f"Preflight failed: could not resolve tools for `{WORKER_AGENT_ID}`: {e}"

    missing = [name for name in tool_names if name not in available]
    if missing:
        return False, (
            f"Preflight failed: `{WORKER_AGENT_ID}` is missing "
            f"{', '.join(f'`{m}`' for m in missing)} under graph `{graph_id}` "
            f"(has: {', '.join(sorted(available)) or 'nothing'})"
        )
    return True, ""


async def preflight_tick(
    repo_descriptor: Dict[str, Any],
    graph_id: str = "coding",
    required_tools: Optional[List[str]] = None,
    cwd: str = "."
) -> Tuple[bool, str, Optional[PushIdentity]]:
    """Runs every gate a tick depends on. Returns (ok, message, push_identity).

    Order matters: the local checks are free, so they run before anything that
    touches the network.
    """
    ok, message = check_required_tools(graph_id, list(required_tools or []))
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
