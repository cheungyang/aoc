import os
from langchain_core.messages import AIMessage
from graphs.content_creation.utils.paths import normalize_project_path
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.adapters import format_gate1_presentation
from .generate_image import generate_image_task
from .draft_plot import draft_plot_task
from .audit_plot import audit_plot_task

async def ideate_package_node(state: dict) -> dict:
    """Macro-Node 2: Generates/reuses Base Image, drafts Video Plot, runs Brand QC Audit, and formats Gate 1 Card."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")

    working_state = dict(state)
    working_state["project_dir"] = project_dir
    working_state["output_dir"] = output_dir

    # Step 2a: Generate or reuse Base Image
    img_res = await generate_image_task(working_state)
    working_state.update(img_res)

    # Step 2b: Draft Video Plot and run self-contained QC Audit (with up to 2 auto-corrections)
    max_qc_reviews = 2
    for attempt in range(max_qc_reviews):
        plot_res = await draft_plot_task(working_state)
        working_state.update(plot_res)

        audit_res = await audit_plot_task(working_state)
        working_state.update(audit_res)

        if working_state.get("video_plot_qc_passed"):
            break

    # Step 2c: Format Gate 1 Presentation Card
    summary = format_gate1_presentation(working_state)

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="🛑 Human-in-the-Loop",
        event_title="Gate 1 Presented",
        details={
            "Base Image": working_state.get("image_path", ""),
            "Video Plot": working_state.get("video_plot_path", ""),
            "QC Passed": working_state.get("video_plot_qc_passed", False),
            "Status": "Awaiting User Decision"
        },
        log_path=execution_log_path
    )

    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "image_path": working_state.get("image_path", ""),
        "video_plot_path": working_state.get("video_plot_path", ""),
        "overlay_text": working_state.get("overlay_text", ""),
        "video_plot_qc_passed": working_state.get("video_plot_qc_passed", True),
        "messages": [AIMessage(content=summary)]
    }
