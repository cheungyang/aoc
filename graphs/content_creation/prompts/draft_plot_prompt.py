def build_draft_plot_prompt(
    topic: str,
    style_str: str,
    image_path: str,
    audio_path: str,
    project_dir: str,
    output_dir: str,
    video_plot_path: str,
    video_plot_json_path: str,
    project_guidelines: str = "",
    char_guidelines: str = "",
    feedback: str = "",
    human_feedback: str = ""
) -> str:
    """Builds structured <playbook>, <current_state>, <assigned_task> prompt for graph-worker plot drafting."""
    playbook_sections = [
        "You are the Content Creator drafting the structured Video Plot.",
        "Strictly adhere to the motion prompt rules, phonetic mouth articulation, character identity guidelines, and standardized template in the creator instructions below.",
        "Output ONLY the exact machine-readable XML <payload> containing the draft markdown or JSON metadata. Do not include conversational filler outside <payload> tags."
    ]
    if project_guidelines:
        playbook_sections.append(f"--- PROJECT CREATOR INSTRUCTIONS ---\n{project_guidelines}")
    if char_guidelines:
        playbook_sections.append(f"--- CHARACTER IDENTITY GUIDELINES ({style_str}) ---\n{char_guidelines}")

    state_sections = [
        f"Topic / Word: `{topic}`",
        f"Episode Style: `{style_str}`",
        f"Source Image: `{image_path}`",
        f"Source Audio: `{audio_path}`",
        f"Project Directory: `{project_dir}`",
        f"Output Directory: `{output_dir}`",
        f"Target Video Plot MD Path: `{video_plot_path}`",
        f"Target Video Plot JSON Path: `{video_plot_json_path}`"
    ]
    if feedback:
        state_sections.append(f"--- BRAND QC FEEDBACK TO FIX ---\n{feedback}")
    if human_feedback:
        state_sections.append(f"--- HUMAN REVISION FEEDBACK (HIGHEST PRIORITY) ---\n{human_feedback}")

    task_instructions = (
        f"Draft the structured Video Plot for topic '{topic}' ({style_str}) with exact image and audio data bindings.\n"
        f"Output the completed plot inside <payload>...</payload>."
    )

    return (
        f"<playbook>\n" + "\n\n".join(playbook_sections) + f"\n</playbook>\n\n"
        f"<current_state>\n" + "\n".join(state_sections) + f"\n</current_state>\n\n"
        f"<assigned_task>\n{task_instructions}\n</assigned_task>"
    )
