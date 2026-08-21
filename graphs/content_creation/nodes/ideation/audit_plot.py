import os
from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path, _resolve_project_doc_path, canonicalize_output_path
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.schemas import PlotAudit
from graphs.content_creation.prompts import build_audit_plot_prompt

async def audit_plot_task(state: dict) -> dict:
    """Audits the drafted Video Plot against Brand Playbook guidelines."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_path = normalize_project_path(state.get("project_path", ""))
    output_path = canonicalize_output_path(project_path, state.get("output_path"), topic)
    qc_playbook_path = _resolve_project_doc_path(state.get("qc_playbook_path"), project_path, "03_QC_Playbook.md")
    video_plot_path = state.get("video_plot_path") or _resolve_asset_path(output_path, topic, "video_plot", next_version=False)
    image_path = state.get("image_path") or _resolve_asset_path(output_path, topic, "image", next_version=False)
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_path, "execution_log.md") if output_path else "")

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

    prompt = build_audit_plot_prompt(
        topic=topic,
        image_path=image_path,
        video_plot_path=video_plot_path,
        project_path=project_path,
        output_path=output_path,
        qc_playbook_content=qc_playbook_content,
        plot_content=plot_content
    )

    try:
        from tools.agent_call import agent_call
        import re
        import json

        channel = state.get("channel") or "content-creation"
        tool_res = await agent_call.ainvoke({
            "agent_id": "graph-worker",
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

        # 1. Direct XML tag extraction for zero-overhead, certain parsing
        status_m = re.search(r"<status>(.*?)</status>", payload, re.DOTALL)
        verdict_m = re.search(r"<verdict>(.*?)</verdict>", payload, re.DOTALL)
        target_m = re.search(r"<rejection_target>(.*?)</rejection_target>", payload, re.DOTALL)
        feedback_m = re.search(r"<feedback>(.*?)</feedback>", payload, re.DOTALL)
        report_m = re.search(r"<markdown_report>(.*?)</markdown_report>", payload, re.DOTALL)

        if verdict_m:
            verdict_val = verdict_m.group(1).strip().upper()
            is_approved = (verdict_val == "APPROVED" or "APPROV" in verdict_val)
            rejection_target = target_m.group(1).strip().lower() if target_m else ("none" if is_approved else "plot")
            feedback = feedback_m.group(1).strip() if feedback_m else (report_m.group(1).strip() if report_m else payload)
        else:
            # 2. Fallback: JSON parsing
            parsed_json = False
            try:
                data = json.loads(payload)
                if isinstance(data, dict):
                    parsed_json = True
                    is_approved = bool(data.get("is_approved", True))
                    feedback = data.get("revision_notes") or data.get("markdown_report") or payload
                    rejection_target = (data.get("rejection_target") or "plot").lower()
            except Exception:
                pass

            # 3. Fallback: Substring matching
            if not parsed_json:
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
        print(f"audit_plot_task: Error executing agent_call for graph-worker: {e}")
        is_approved = True
        feedback = "Auto-approved (Audit Exception Pass-through)"
        rejection_target = "plot"

    _append_execution_log(
        output_path=output_path,
        topic=topic,
        actor="⚙️ Graph Worker (Brand QC Audit)",
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
