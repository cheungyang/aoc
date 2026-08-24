import os
from typing import Dict, Any
from graphs.coding.schemas import CodingState
from graphs.coding.prompts.critic_prompt import build_critic_prompt
from graphs.coding.utils.xml_parsers import parse_critic_verdict_xml
from graphs.coding.utils.token_opt import (
    sanitize_diff,
    check_static_bloated_files,
    check_static_silent_failures
)
from graphs.coding.utils import git_ops

async def critic_node(state: CodingState) -> Dict[str, Any]:
    """
    Critic QA Node (Goldfish 2):
    Audits the git diff against the master spec for LLM shortcuts and anti-patterns:
    1. Fake It Trap (mock dummy responses)
    2. Happy Path Bias (missing error handling)
    3. Silent Failure (swallowed catch/except blocks)
    4. Bloated Files (>150 lines without modularization)
    """
    workspace_path = state.get("workspace_path", "")
    current_attempt = state.get("attempt_count", 0)
    modified_files = state.get("modified_files", [])

    # 1. Fetch git diff
    raw_diff = await git_ops.get_git_diff(workspace_path)
    clean_diff = sanitize_diff(raw_diff)

    # 2. Run deterministic static checks FIRST (saves 100% of LLM tokens on obvious anti-patterns)
    bloated = check_static_bloated_files(workspace_path, modified_files, max_lines=150)
    if bloated:
        evidence_str = "\n".join(f"- {b['file']}: {b['evidence']}" for b in bloated)
        feedback = f"STATIC QA REJECTION (Bloated Files):\n{evidence_str}\nPlease modularize files exceeding 150 lines."
        return {
            "critic_passed": False,
            "critic_feedback": feedback,
            "diff_summary": clean_diff,
            "attempt_count": current_attempt + 1
        }

    silent_failures = check_static_silent_failures(workspace_path, modified_files)
    if silent_failures:
        evidence_str = "\n".join(f"- {s['file']} (L{s['line_numbers']}): {s['evidence']}" for s in silent_failures)
        feedback = f"STATIC QA REJECTION (Silent Failure):\n{evidence_str}\nDo not swallow errors silently. Implement proper error logging and bubbling."
        return {
            "critic_passed": False,
            "critic_feedback": feedback,
            "diff_summary": clean_diff,
            "attempt_count": current_attempt + 1
        }

    # 3. Read master spec text / ground truth
    current_task = state.get("current_task") or {}
    task_id = current_task.get("task_id")
    if not task_id:
        return {
            "critic_passed": False,
            "critic_feedback": "Critic QA failed: Missing required 'task_id' in current_task state.",
            "error_message": "Missing required 'task_id' in current_task state.",
            "attempt_count": current_attempt + 1
        }
    spec_path = state.get("master_spec_path") or current_task.get("spec_path", "")
    allowed_files = current_task.get("allowed_files", [])
    acceptance_criteria = current_task.get("acceptance_criteria", "")

    spec_content = ""
    project_path = state.get("project_path", "")
    target_spec_path = state.get("master_spec_path") or spec_path
    if target_spec_path and not os.path.isabs(target_spec_path) and project_path:
        target_spec_path = os.path.join(project_path, target_spec_path)
    if target_spec_path and os.path.exists(target_spec_path):
        try:
            with open(target_spec_path, "r", encoding="utf-8") as f:
                spec_content = f.read()
        except Exception:
            pass

    # If spec file not found, use task envelope criteria
    if not spec_content and acceptance_criteria:
        spec_content = (
            f"# Task: {task_id}\n"
            f"Allowed Files: {', '.join(allowed_files)}\n\n"
            f"## Acceptance Criteria\n{acceptance_criteria}"
        )

    if not spec_content:
        feedback = f"Critic QA failed: No specification text or acceptance criteria available for task {task_id}."
        return {
            "critic_passed": False,
            "critic_feedback": feedback,
            "diff_summary": clean_diff,
            "attempt_count": current_attempt + 1
        }

    # 4. Invoke LLM Critic
    prompt = build_critic_prompt(spec_text=spec_content, git_diff_text=clean_diff or "(No git diff changes)")
    channel = state.get("channel") or "coding-pipeline"

    try:
        from tools.agent_call import agent_call
        tool_res = await agent_call.ainvoke({
            "agent_id": "graph-worker",
            "prompt": prompt,
            "channel": channel
        })
        parsed = parse_critic_verdict_xml(str(tool_res))
    except Exception as e:
        print(f"critic_node: agent_call error: {e}")
        # Fail-closed: do not approve unverified code on failure
        parsed = {
            "verdict": "REJECT",
            "passed": False,
            "anti_patterns_detected": [],
            "feedback_for_worker": f"Critic QA audit failed to complete: {e}"
        }

    passed = parsed["passed"]
    feedback = parsed["feedback_for_worker"]
    if parsed["anti_patterns_detected"]:
        patterns_str = "\n".join(
            f"- [{p['rule']}] {p['file']} (L{p['line_numbers']}): {p['evidence']}"
            for p in parsed["anti_patterns_detected"]
        )
        feedback = f"{patterns_str}\n\nRemediation:\n{feedback}"

    new_attempt = current_attempt if passed else current_attempt + 1

    return {
        "critic_passed": passed,
        "critic_feedback": feedback if not passed else "",
        "diff_summary": clean_diff,
        "attempt_count": new_attempt
    }
