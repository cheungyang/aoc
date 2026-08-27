import os
from typing import Dict, Any, List
from graphs.coding.schemas import CodingState
from graphs.coding.prompts.coder_prompt import build_coder_prompt
from graphs.coding.utils.xml_parsers import parse_worker_handoff_xml
from graphs.coding.utils.token_opt import sanitize_traceback
from graphs.coding.utils.git_ops import run_cmd_async

async def worker_node(state: CodingState) -> Dict[str, Any]:
    """
    Coder Worker Node (Goldfish 1):
    Prompts the stateless graph-worker to implement code and tests strictly
    within the isolated worktree boundaries, injecting any failure/revision feedback.
    """
    if state.get("error_message") and not state.get("workspace_path"):
        return {}

    workspace_path = state.get("workspace_path", "")
    current_task = state.get("current_task") or {}
    task_id = current_task.get("task_id")
    if not task_id:
        return {
            "error_message": "Worker node error: Missing required 'task_id' in current_task state."
        }
    spec_path = current_task.get("spec_path", "")
    allowed_files = current_task.get("allowed_files", [])
    acceptance_criteria = current_task.get("acceptance_criteria", "")
    verification_command = current_task.get("verification_command", "")
    
    # Read spec text from pre-resolved spec_path
    spec_content = ""
    target_spec_path = state.get("spec_path") or spec_path
    if target_spec_path and os.path.exists(target_spec_path):
        try:
            with open(target_spec_path, "r", encoding="utf-8") as f:
                spec_content = f.read()
        except Exception:
            pass

    # Sanitize retry delta to prevent token bloat
    raw_stderr = state.get("test_stderr", "")
    clean_stderr = sanitize_traceback(raw_stderr) if raw_stderr else None
    critic_feedback = state.get("critic_feedback")
    human_feedback = state.get("latest_human_feedback")
    pr_comments = state.get("github_pr_comments") or []
    if pr_comments and not human_feedback:
        human_feedback = "GitHub PR Review Comments:\n" + "\n".join(pr_comments)

    prompt = build_coder_prompt(
        workspace_path=workspace_path,
        task_id=task_id,
        spec_path=spec_path,
        allowed_files=allowed_files,
        acceptance_criteria=acceptance_criteria,
        verification_command=verification_command,
        spec_content=spec_content,
        test_stderr=clean_stderr,
        critic_feedback=critic_feedback,
        human_feedback=human_feedback
    )

    channel = state.get("channel") or "coding-pipeline"
    modified_files: List[str] = []
    summary = ""
    agent_error = ""

    try:
        from tools.agent_call import agent_call
        tool_res = await agent_call.ainvoke({
            "agent_id": "graph-worker",
            "prompt": prompt,
            "channel": channel
        })
        parsed = parse_worker_handoff_xml(str(tool_res))
        modified_files = parsed.get("modified_files", [])
        summary = parsed.get("implementation_summary", "")
    except Exception as e:
        print(f"worker_node: agent_call error: {e}")
        agent_error = str(e)

    # Inspect git status if modified_files not explicitly listed in XML
    if not modified_files and workspace_path and os.path.exists(workspace_path):
        code, out, _ = await run_cmd_async(["git", "status", "--porcelain"], cwd=workspace_path, timeout=10.0)
        if code == 0 and out.strip():
            for line in out.strip().splitlines():
                parts = line.strip().split(maxsplit=1)
                if len(parts) == 2:
                    modified_files.append(parts[1])

    # Dynamic summary determination based on real evidence
    if not summary:
        if agent_error:
            summary = f"Worker execution error: {agent_error}"
        elif modified_files:
            summary = f"Worker modified {len(modified_files)} file(s) without structured summary XML."
        else:
            summary = "Worker completed without modifying files."

    return {
        "modified_files": modified_files,
        "implementation_summary": summary,
        # Clear prior retry flags now that worker has produced fresh code
        "test_stderr": "",
        "critic_feedback": "",
        "latest_human_feedback": "",
        "github_pr_comments": []
    }
