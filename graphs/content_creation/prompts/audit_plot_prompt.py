def build_audit_plot_prompt(
    topic: str,
    image_path: str,
    video_plot_path: str,
    project_dir: str,
    output_dir: str,
    qc_playbook_content: str = "",
    plot_content: str = ""
) -> str:
    """Builds structured <playbook>, <current_state>, <assigned_task> prompt for graph-worker brand QC audit with strict XML output format."""
    playbook_sections = [
        "You are the Brand Editor auditing a Video Plot before presenting it at HITL Gate 1.",
        "Evaluate the plot rigorously against all playbook criteria (visual consistency, duration match, phonetic mouth articulation, aspect ratio).",
        "Output ONLY a machine-readable XML payload wrapped in <payload>...</payload> adhering strictly to the schema below without any conversational text outside the tags."
    ]
    if qc_playbook_content:
        playbook_sections.append(f"--- QC PLAYBOOK ---\n{qc_playbook_content}")

    state_sections = [
        f"Topic / Word: `{topic}`",
        f"Target Base Image File: `{image_path}`",
        f"Drafted Video Plot Path: `{video_plot_path}`",
        f"Project Directory: `{project_dir}`",
        f"Output Directory: `{output_dir}`"
    ]
    if plot_content:
        state_sections.append(f"--- DRAFTED VIDEO PLOT CONTENT ---\n{plot_content}")

    task_instructions = (
        f"Audit the drafted Video Plot for '{topic}' against the QC Playbook and base image.\n\n"
        "MANDATORY OUTPUT FORMAT:\n"
        "You MUST format your entire response within <payload>...</payload> using the following exact XML schema (fill each tag with your generated result):\n"
        "<payload>\n"
        "<status>{success|error}</status>\n"
        "<error>{error_details_if_any_else_empty}</error>\n"
        "<verdict>{APPROVED|REJECTED}</verdict>\n"
        "<rejection_target>{none|plot|image|both}</rejection_target>\n"
        "<feedback>{detailed_actionable_qc_feedback_or_approval_summary}</feedback>\n"
        "</payload>"
    )

    return (
        f"<playbook>\n" + "\n\n".join(playbook_sections) + f"\n</playbook>\n\n"
        f"<current_state>\n" + "\n".join(state_sections) + f"\n</current_state>\n\n"
        f"<assigned_task>\n{task_instructions}\n</assigned_task>"
    )
