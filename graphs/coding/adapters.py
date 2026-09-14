import os
import re
from typing import Dict, Any, Optional
from langchain_core.messages import HumanMessage, AIMessage
from graphs.coding.schemas import CodingState
from graphs.coding.utils.dag import MANIFEST_FILENAME, manifest_path_for_project

def prepare_input(query: str, caller: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """Translates incoming text query / kwargs into initial CodingState."""
    if caller and "<caller>" not in query:
        formatted_query = f"<caller>{caller}</caller>\n{query}"
    else:
        formatted_query = query

    # Extract project_name
    project_name = kwargs.get("project_name") or kwargs.get("project") or ""
    if not project_name:
        m_proj = re.search(r'(?:project_name|project)[:=]\s*["\']?([^"\'\s,]+)["\']?', query, re.IGNORECASE)
        if m_proj:
            project_name = m_proj.group(1).strip()

    # Extract project_path (Spec directory: pkm/wiki/software/<project>)
    project_path = kwargs.get("project_path") or ""
    if not project_path:
        m_dir = re.search(r'project_path[:=]\s*["\']?([^"\'\s,]+)["\']?', query, re.IGNORECASE)
        if m_dir:
            project_path = m_dir.group(1).strip()

    if not project_path and project_name:
        project_path = os.path.join("pkm", "wiki", "software", project_name)

    if project_path:
        project_path = os.path.abspath(project_path)

    # Extract build_request_path. One project, one manifest: a run is told which
    # queue it is working on, and there is no global one to fall back to.
    build_request_path = kwargs.get("build_request_path") or kwargs.get("manifest_path") or ""
    if not build_request_path:
        m_req = re.search(r'(?:build_request_path|manifest_path|manifest)[:=]\s*["\']?([^"\'\s,]+)["\']?', query, re.IGNORECASE)
        if m_req:
            build_request_path = m_req.group(1).strip()

    if build_request_path:
        build_request_path = os.path.abspath(build_request_path)
    elif project_name:
        build_request_path = manifest_path_for_project(project_name)
    elif project_path:
        build_request_path = os.path.join(project_path, MANIFEST_FILENAME)

    # Extract target_repo (e.g. owner/repo)
    target_repo = kwargs.get("target_repo") or kwargs.get("repo") or ""
    if not target_repo:
        m_repo = re.search(r'(?:target_repo|repo)[:=]\s*["\']?([a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-]+)["\']?', query, re.IGNORECASE)
        if m_repo:
            target_repo = m_repo.group(1).strip()

    max_concurrency = int(kwargs.get("max_concurrency") or 1)
    session_id = kwargs.get("session_id") or ""
    thread_id = kwargs.get("thread_id") or session_id
    channel = kwargs.get("channel") or "coding-pipeline"

    # The manifest carries each task's spec_path, so a tick does not need a
    # project directory — but it does need to know *which project*. Which
    # manifest is the one required input, the way `topic` is for content
    # creation: without it a run has no queue, and guessing one means working
    # through some other project's tasks.
    error_msg = ""
    if not build_request_path:
        error_msg = (
            "Missing required build_request.json. The coding graph works on one "
            "project at a time — pass `build_request_path: "
            "'pkm/wiki/software/<project>/build_request.json'`, or "
            "`project_name: '<project>'` to resolve it."
        )

    # A nudge in chat is not an approval — approval is read off GitHub — but it
    # does reopen the polling window and is passed to the worker as feedback.
    human_feedback = kwargs.get("latest_human_feedback") or kwargs.get("feedback") or ""
    if not human_feedback and query:
        clean_q = query.replace("<caller>", "").replace("</caller>", "").strip()
        if any(w in clean_q.lower() for w in ["approve", "lgtm", "yes", "revise", "abort", "cancel", "proceed"]):
            human_feedback = clean_q

    graph_config = _load_graph_config()
    manifest_settings = _load_manifest_settings(build_request_path)

    return {
        "build_request_path": build_request_path,
        "project_name": project_name or manifest_settings.get("project_name", ""),
        "target_repo": target_repo,
        "repo": manifest_settings.get("repo") or {},
        "reviewers": kwargs.get("reviewers") or manifest_settings.get("reviewers") or [],
        "approval_signals": manifest_settings.get("approval_signals"),
        "rejection_signals": manifest_settings.get("rejection_signals"),
        "project_path": project_path,
        "max_concurrency": max_concurrency,
        "session_id": session_id,
        "thread_id": thread_id,
        "channel": channel,
        "graph_id": graph_config.get("graph_id", "coding"),
        # The graph's `tools` grant is the requirement: preflight asserts the
        # worker actually receives what this graph hands it. Deriving it here
        # means there is no second list that can drift from the grant.
        "required_tools": sorted((graph_config.get("tools") or {}).keys()),
        "queue": kwargs.get("queue") or [],
        "completed_tasks": [],
        "failed_tasks": [],
        "tick_report": [],
        "tick_handled": [],
        "test_run_passed": False,
        "latest_human_feedback": human_feedback,
        "messages": [HumanMessage(content=formatted_query)],
        "error_message": error_msg
    }


def _load_graph_config() -> Dict[str, Any]:
    """Reads graph.json next to this module (graph id and the tool grant)."""
    import json
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "graph.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_manifest_settings(build_request_path: str) -> Dict[str, Any]:
    """Reads the review-related settings the manifest owns.

    Read-only and failure-tolerant: a missing manifest simply means no reviewers
    configured, and the scheduler reports the empty queue.
    """
    from graphs.coding.utils.manifest import load_manifest
    from graphs.coding.utils.repo import get_repo_descriptor

    try:
        manifest = load_manifest(build_request_path)
    except Exception:
        return {}

    return {
        "project_name": manifest.get("project_name", ""),
        "repo": get_repo_descriptor(manifest),
        "reviewers": manifest.get("reviewers") or [],
        "approval_signals": manifest.get("approval_signals"),
        "rejection_signals": manifest.get("rejection_signals"),
    }


def format_tick_report(state: Dict[str, Any]) -> str:
    """Renders the lines a tick produced.

    Returns an empty string when the tick did nothing. The scheduled runner
    treats empty stdout as "post nothing", so a quiet pipeline stays quiet
    instead of spamming a channel every five minutes.
    """
    lines = [str(line).rstrip() for line in (state.get("tick_report") or []) if str(line).strip()]
    if not lines:
        return ""
    return "\n".join(lines)


def format_output(state: Dict[str, Any]) -> str:
    """Renders the result of a tick.

    Empty output is meaningful, not a bug: the scheduled runner treats empty
    stdout as "post nothing", which is what keeps a channel usable when the
    queue is idle 287 times out of 288 a day.
    """
    if not isinstance(state, dict):
        return str(state)

    if state.get("error_message"):
        return f"🛑 Coding tick error: {state['error_message']}"

    return format_tick_report(state)
