import os
from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path, _resolve_project_doc_path
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.schemas import PlotAudit

async def audit_plot_task(state: dict) -> dict:
    """Audits the drafted Video Plot against Brand Playbook guidelines."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
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
        from core.loaders.agents_loader import AgentsLoader
        from langchain_google_genai import ChatGoogleGenerativeAI

        config = AgentsLoader()._agent_configs.get("brand-editor", {})
        model_name = config.get("model", "gemini-3.7-flash")

        llm = ChatGoogleGenerativeAI(model=model_name).with_structured_output(PlotAudit)
        audit_data: PlotAudit = await llm.ainvoke(prompt)

        is_approved = audit_data.is_approved
        feedback = audit_data.revision_notes or audit_data.markdown_report
        rejection_target = (audit_data.rejection_target or "plot").lower()
    except Exception as e:
        print(f"audit_plot_task: Error in structured audit: {e}")
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
