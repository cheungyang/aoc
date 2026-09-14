"""audit — advisory anti-pattern review of the diff.

Always advisory, and that is a deliberate demotion. The critic is good at
catching a fake implementation that passes its own tests, and bad at judging
whether a 200-line file is a problem — so it writes its opinion into the PR for
the human reviewer instead of blocking the pipeline on it.

That is not configurable. A mode that could gate the pipeline on this verdict
would be an untested path behind a flag, one parser slip away from bouncing
correct work back to the worker, so the node records what it found and always
continues to publish.
"""
import os
from typing import Any, Dict

from graphs.coding.prompts.critic_prompt import build_critic_prompt
from graphs.coding.schemas import CodingState
from graphs.coding.utils import manifest as manifest_store
from graphs.coding.utils.dag import resolve_manifest_path
from graphs.coding.utils.token_opt import sanitize_diff
from graphs.coding.utils.worker import call_worker
from graphs.coding.utils.xml_parsers import parse_critic_verdict_xml
from core.util import git_ops


async def audit_node(state: CodingState) -> Dict[str, Any]:
    manifest_path = resolve_manifest_path(state.get("build_request_path"))
    current_task = state.get("current_task") or {}
    task_id = current_task.get("task_id")
    workspace_path = state.get("workspace_path") or ""
    report = list(state.get("tick_report") or [])

    if not task_id:
        return {"error_message": "audit: current_task has no task_id.", "route": "done"}

    # Guard: the audit already ran for this tree.
    if manifest_store.stage_at_or_past(current_task, "audited"):
        return {"stage": "audited", "route": "publish", "tick_report": report, "error_message": ""}

    diff = sanitize_diff(await git_ops.get_git_diff(workspace_path)) if workspace_path else ""
    spec_content = _spec_text(state, current_task)

    verdict = await _run_audit(
        spec_content=spec_content,
        diff=diff,
        channel=state.get("channel") or "coding-pipeline",
        graph_id=state.get("graph_id") or "coding"
    )
    passed = verdict["passed"]
    feedback = verdict["feedback"]

    manifest_store.persist_task(manifest_path, task_id, stage="audited")

    if passed:
        report.append(f"🔎 `{task_id}`: audit found nothing.")
        return {"stage": "audited", "route": "publish", "audit_passed": True, "audit_feedback": "",
                "diff_summary": diff, "tick_report": report, "error_message": ""}

    # Not a gate: the finding travels to the PR as a comment (publish posts
    # it) so the human reviewer decides.
    report.append(f"🔎 `{task_id}`: audit raised concerns (advisory) — posted to the PR.")
    return {"stage": "audited", "route": "publish", "audit_passed": False, "audit_feedback": feedback,
            "diff_summary": diff, "tick_report": report, "error_message": ""}


def _spec_text(state: CodingState, current_task: Dict[str, Any]) -> str:
    path = state.get("spec_path") or current_task.get("spec_path", "")
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass

    criteria = current_task.get("acceptance_criteria", "")
    if criteria:
        return (
            f"# Task: {current_task.get('task_id')}\n"
            f"Allowed Files: {', '.join(current_task.get('allowed_files') or [])}\n\n"
            f"## Acceptance Criteria\n{criteria}"
        )
    return ""


async def _run_audit(spec_content: str, diff: str, channel: str, graph_id: str = "coding") -> Dict[str, Any]:
    """Asks the model for a verdict. An audit that cannot run is not a rejection.

    The old critic failed closed and blocked the pipeline whenever the LLM call
    errored. Now that the audit is advisory, treating an outage as "concerns
    raised" would spam every PR with a message about the auditor, not the code.
    """
    if not spec_content:
        return {"passed": True, "feedback": ""}

    prompt = build_critic_prompt(
        spec_text=spec_content,
        git_diff_text=diff or "(No git diff changes)"
    )
    try:
        result = await call_worker(prompt=prompt, graph_id=graph_id, channel=channel)
        parsed = parse_critic_verdict_xml(result)
    except Exception as e:
        print(f"audit: agent_call error: {e}")
        return {"passed": True, "feedback": ""}

    feedback = parsed.get("feedback_for_worker", "")
    patterns = parsed.get("anti_patterns_detected") or []
    if patterns:
        rendered = "\n".join(
            f"- [{p.get('rule')}] {p.get('file')} (L{p.get('line_numbers')}): {p.get('evidence')}"
            for p in patterns
        )
        feedback = f"{rendered}\n\nRemediation:\n{feedback}"

    return {"passed": bool(parsed.get("passed")), "feedback": feedback}
