import os
import re
from typing import Dict, Any, Optional
from langchain_core.messages import HumanMessage, AIMessage
from graphs.coding.schemas import CodingState

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

    # Extract build_request_path
    build_request_path = kwargs.get("build_request_path") or kwargs.get("manifest_path") or ""
    if not build_request_path:
        m_req = re.search(r'(?:build_request_path|manifest_path|manifest)[:=]\s*["\']?([^"\'\s,]+)["\']?', query, re.IGNORECASE)
        if m_req:
            build_request_path = m_req.group(1).strip()

    if not build_request_path:
        build_request_path = os.path.abspath("pkm/wiki/software/build_request.json")
    else:
        build_request_path = os.path.abspath(build_request_path)

    # Extract target_repo (e.g. owner/repo)
    target_repo = kwargs.get("target_repo") or kwargs.get("repo") or ""
    if not target_repo:
        m_repo = re.search(r'(?:target_repo|repo)[:=]\s*["\']?([a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-]+)["\']?', query, re.IGNORECASE)
        if m_repo:
            target_repo = m_repo.group(1).strip()

    max_concurrency = int(kwargs.get("max_concurrency") or 1)
    max_retries = int(kwargs.get("max_retries") or 3)
    session_id = kwargs.get("session_id") or ""
    thread_id = kwargs.get("thread_id") or session_id
    channel = kwargs.get("channel") or "coding-pipeline"

    # Validation: project_path must be explicitly provided or resolvable from project_name
    error_msg = ""
    if not project_path:
        error_msg = "Initialization error: 'project_path' is required and must be explicitly provided at graph initialization."

    # Check for human feedback / resume message
    human_feedback = kwargs.get("latest_human_feedback") or kwargs.get("feedback") or ""
    if not human_feedback and query:
        clean_q = query.replace("<caller>", "").replace("</caller>", "").strip()
        if any(w in clean_q.lower() for w in ["approve", "lgtm", "yes", "revise", "abort", "cancel", "proceed"]):
            human_feedback = clean_q

    return {
        "build_request_path": build_request_path,
        "project_name": project_name,
        "target_repo": target_repo,
        "project_path": project_path,
        "max_concurrency": max_concurrency,
        "max_retries": max_retries,
        "session_id": session_id,
        "thread_id": thread_id,
        "channel": channel,
        "queue": kwargs.get("queue") or [],
        "active_runs": {},
        "completed_tasks": [],
        "failed_tasks": [],
        "attempt_count": 0,
        "test_run_passed": False,
        "critic_passed": False,
        "latest_human_feedback": human_feedback,
        "hitl_decision": "",
        "messages": [HumanMessage(content=formatted_query)],
        "error_message": error_msg
    }


def format_hitl_presentation(state: Dict[str, Any]) -> str:
    """Generates the Markdown presentation string for HITL Review Gate (v2)."""
    current_task = state.get("current_task") or {}
    task_id = current_task.get("task_id", "Unknown Task")
    run_id = state.get("run_id", "run_default")
    branch = state.get("branch_name", "feat/unknown")
    pr_url = state.get("pr_url") or current_task.get("pr_url", "(PR pending creation)")

    test_passed = state.get("test_run_passed", False)
    test_status_str = "✅ ALL TESTS PASSING" if test_passed else "❌ TESTS FAILED"

    critic_passed = state.get("critic_passed", False)
    critic_status_str = "✅ APPROVED" if critic_passed else "⚠️ REJECTED"

    return (
        f"### 🔍 Coding Graph HITL Review Gate\n\n"
        f"- **Task ID**: `{task_id}`\n"
        f"- **Branch**: `{branch}`\n"
        f"- **GitHub PR**: 🔗 [{pr_url}]({pr_url})\n"
        f"- **Test Suite**: {test_status_str}\n"
        f"- **Critic Verdict**: {critic_status_str}\n\n"
        f"Please review the changes on GitHub.\n"
        f"- **Approve**: Click **\"Approve\"** on GitHub PR OR reply `Approve` in chat to merge into `origin/main`.\n"
        f"- **Revise**: Leave review comments on the GitHub PR or reply with feedback here to request updates."
    )


def format_output(state: Dict[str, Any]) -> str:
    """Extracts final reply text from CodingState."""
    if not isinstance(state, dict):
        return str(state)

    if state.get("error_message"):
        return f"🛑 Coding graph execution error: {state['error_message']}"

    # If currently paused at HITL Gate
    if state.get("hitl_decision") == "pending_review":
        return format_hitl_presentation(state)

    # If completed and Commit URL exists
    if state.get("commit_url"):
        completed = state.get("completed_tasks", [])
        return (
            f"🎉 **Coding Execution Completed & Merged!**\n\n"
            f"- **Tasks Completed**: `{', '.join(completed)}`\n"
            f"- **Pull Request**: {state.get('pr_url')}\n"
            f"- **Merged Commit on Main**: {state.get('commit_url')}\n"
            f"- **Status**: ✅ Completed & Verified."
        )

    if state.get("pr_url") and not state.get("current_task"):
        completed = state.get("completed_tasks", [])
        return (
            f"🎉 **Coding Execution Completed!**\n\n"
            f"- **Tasks Completed**: `{', '.join(completed)}`\n"
            f"- **Pull Request**: {state['pr_url']}\n"
            f"- **Status**: ✅ Ready for merge."
        )

    if "messages" in state and state["messages"]:
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage):
                return msg.content
            elif isinstance(msg, dict) and msg.get("role") == "assistant":
                return msg.get("content", "")

    return str(state)
