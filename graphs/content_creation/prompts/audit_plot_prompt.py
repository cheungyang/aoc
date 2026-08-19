def build_audit_plot_prompt(
    topic: str,
    image_path: str,
    video_plot_path: str,
    project_dir: str,
    output_dir: str,
    qc_playbook_content: str = "",
    plot_content: str = ""
) -> str:
    """Builds structured <playbook>, <current_state>, <assigned_task> prompt for graph-worker brand QC audit."""
    playbook_sections = [
        "You are the Brand Editor auditing a Video Plot before presenting it at HITL Gate 1.",
        "Evaluate the plot rigorously against all playbook criteria (visual consistency, duration match, phonetic mouth articulation, aspect ratio).",
        "Output ONLY a machine-readable verdict inside <payload>...</payload>. Do not include conversational filler outside <payload> tags.",
        "Output format: JSON with keys `is_approved` (bool), `revision_notes` (str), `rejection_target` ('plot'|'image'|'both'|'none'), or text format (e.g. `VERDICT: APPROVED` or `VERDICT: REJECTED TARGET: <IMAGE|PLOT|BOTH>`)."
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
        f"Audit the drafted Video Plot for '{topic}' against the QC Playbook and base image.\n"
        f"Output your verdict strictly inside <payload>...</payload>."
    )

    return (
        f"<playbook>\n" + "\n\n".join(playbook_sections) + f"\n</playbook>\n\n"
        f"<current_state>\n" + "\n".join(state_sections) + f"\n</current_state>\n\n"
        f"<assigned_task>\n{task_instructions}\n</assigned_task>"
    )
