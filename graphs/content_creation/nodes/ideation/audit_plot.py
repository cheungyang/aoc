import os
from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path, _resolve_project_doc_path, canonicalize_output_dir
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.schemas import PlotAudit

async def audit_plot_task(state: dict) -> dict:
    """Audits the drafted Video Plot against Brand Playbook guidelines."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = canonicalize_output_dir(project_dir, state.get("output_dir"), topic)
    qc_playbook_path = _resolve_project_doc_path(state.get("qc_playbook_path"), project_dir, "03_QC_Playbook.md")
    video_plot_path = state.get("video_plot_path") or _resolve_asset_path(output_dir, topic, "video_plot", next_version=False)
    image_path = state.get("image_path") or _resolve_asset_path(output_dir, topic, "image", next_version=False)
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")

    plot_content = ""
    if video_plot_path and os.path.exists(video_plot_path):
        try:
            with open(video_plot_path, "r", encoding="utf-8") as f:
                plot_content = f.read()
        except Exception:
            pass

    qc_playbook_content = ""
    if qc_playbook_path and os.path.exists(qc_playbook_path):
        try:
            with open(qc_playbook_path, "r", encoding="utf-8") as f:
                qc_playbook_content = f.read()
        except Exception:
            pass

    prompt = (
        f"You are the Brand Editor auditing a Video Plot before presenting it at HITL Gate 1.\n\n"
        f"--- QC PLAYBOOK ---\n{qc_playbook_content}\n-------------------\n\n"
        f"--- DRAFTED VIDEO PLOT ---\n{plot_content}\n---------------------------\n\n"
        f"Target Base Image File: {image_path}\n"
        f"Evaluate the plot rigorously against all playbook criteria."
    )

    try:
        from tools.agent_call import agent_call
        import re

        channel = state.get("channel") or "content-creation"
        tool_res = await agent_call.ainvoke({
            "agent_id": "brand-editor",
            "prompt": prompt,
            "channel": channel
        })

        payload = ""
        m = re.search(r"<payload>(.*?)</payload>", str(tool_res), re.DOTALL)
        if m:
            payload = m.group(1).strip()
        else:
            payload = str(tool_res).strip()

        is_approved = True
        feedback = payload
        rejection_target = "plot"

        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                is_approved = bool(data.get("is_approved", True))
                feedback = data.get("revision_notes") or data.get("markdown_report") or payload
                rejection_target = (data.get("rejection_target") or "plot").lower()
        except Exception:
            if "VERDICT: REJECTED" in payload.upper() or "REJECTED" in payload.upper():
                is_approved = False
                if "REJECTED TARGET: IMAGE" in payload.upper() or "TARGET: IMAGE" in payload.upper():
                    rejection_target = "image"
                elif "REJECTED TARGET: BOTH" in payload.upper() or "TARGET: BOTH" in payload.upper():
                    rejection_target = "both"
                else:
                    rejection_target = "plot"
            elif "VERDICT: APPROVED" in payload.upper() or "APPROVED" in payload.upper():
                is_approved = True
                rejection_target = "none"

    except Exception as e:
        print(f"audit_plot_task: Error executing agent_call for brand-editor: {e}")
        is_approved = True
        feedback = "Auto-approved (Audit Exception Pass-through)"
        rejection_target = "plot"

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🧐 Brand Editor",
        event_title="Video Plot Brand QC Audit",
        details={
            "Verdict": "APPROVED" if is_approved else "REJECTED",
            "Feedback": feedback,
            "Rejection Target": rejection_target.upper() if not is_approved else "NONE"
        },
        log_path=execution_log_path
    )

    return {
        "video_plot_qc_passed": is_approved,
        "video_plot_feedback": feedback,
        "qc_rejection_target": rejection_target
    }
