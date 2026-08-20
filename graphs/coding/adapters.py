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

    # Extract build_request_path
    build_request_path = kwargs.get("build_request_path") or kwargs.get("manifest_path") or ""
    if not build_request_path:
        m_req = re.search(r'(?:build_request_path|manifest_path|manifest)[:=]\s*["\']?([^"\'\s,]+)["\']?', query, re.IGNORECASE)
        if m_req:
            build_request_path = m_req.group(1).strip()
        else:
            build_request_path = "pkm/wiki/software/build_request.json"

    # Extract project_path
    project_path = kwargs.get("project_path") or ""
    if not project_path:
        m_dir = re.search(r'project_path[:=]\s*["\']?([^"\'\s,]+)["\']?', query, re.IGNORECASE)
        if m_dir:
            project_path = m_dir.group(1).strip()

    # Extract project_name
    project_name = kwargs.get("project_name") or kwargs.get("project") or ""
    if not project_name:
        m_proj = re.search(r'(?:project_name|project)[:=]\s*["\']?([^"\'\s,]+)["\']?', query, re.IGNORECASE)
        if m_proj:
            project_name = m_proj.group(1).strip()

    max_concurrency = int(kwargs.get("max_concurrency") or 1)
    max_retries = int(kwargs.get("max_retries") or 3)
    session_id = kwargs.get("session_id") or ""
    thread_id = kwargs.get("thread_id") or session_id
    channel = kwargs.get("channel") or "coding-pipeline"

    # Validation: project_path must be explicitly provided
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
    """Generates the Markdown presentation string for HITL Review Gate."""
    current_task = state.get("current_task") or {}
    task_id = current_task.get("task_id", "Unknown Task")
    run_id = state.get("run_id", "run_default")
    branch = state.get("branch_name", "feat/unknown")
    diff = state.get("diff_summary", "(No git diff)")

    test_passed = state.get("test_run_passed", False)
    test_status_str = "✅ ALL TESTS PASSING" if test_passed else "❌ TESTS FAILED"
    test_out = state.get("test_stdout") or state.get("test_stderr") or ("(Test passed without stdout)" if test_passed else "(No test output captured)")

    critic_passed = state.get("critic_passed", False)
    critic_status_str = "✅ APPROVED (No anti-patterns found)" if critic_passed else "⚠️ REJECTED (Anti-patterns detected)"
    crit_feedback = state.get("critic_feedback") or ("No anti-patterns detected." if critic_passed else "Anti-patterns or quality issues detected.")

    return (
        f"### 🔍 Coding Graph HITL Review Gate\n\n"
        f"- **Task ID**: `{task_id}`\n"
        f"- **Run ID**: `{run_id}`\n"
        f"- **Branch**: `{branch}`\n"
        f"- **Test Suite**: {test_status_str}\n"
        f"- **Critic Verdict**: {critic_status_str}\n\n"
        f"#### Test Output Preview:\n"
        f"```text\n{test_out[:400] + ('...' if len(test_out) > 400 else '')}\n```\n\n"
        f"#### Diff Summary:\n"
        f"```diff\n{diff[:1500] + ('\n... (truncated)' if len(diff) > 1500 else '')}\n```\n\n"
        f"*Reply **'Approve'** to commit & push PR, or provide revision feedback to refine the implementation.*"
    )


def format_output(state: Dict[str, Any]) -> str:
    """Extracts final reply text from CodingState."""
    if not isinstance(state, dict):
        return str(state)

    if state.get("error_message"):
        return f"🛑 Coding graph execution error: {state['error_message']}"

    # If currently paused at HITL Gate
    if state.get("critic_passed") and state.get("test_run_passed") and not state.get("pr_url"):
        return format_hitl_presentation(state)

    # If completed and PR URL exists
    if state.get("pr_url"):
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
