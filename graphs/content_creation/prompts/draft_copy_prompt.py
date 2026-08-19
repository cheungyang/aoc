def build_draft_copy_prompt(
    topic: str,
    project_dir: str,
    output_dir: str,
    copy_path: str,
    copy_json_path: str,
    instructions_text: str = "",
    human_feedback: str = "",
    is_revision: bool = False
) -> str:
    """Builds structured <playbook>, <current_state>, <assigned_task> prompt for graph-worker copywriting."""
    playbook_sections = [
        f"You are the Content Creator drafting social media publication copy for the topic '{topic}'.",
        "Draft engaging social post title, caption, Cantonese/English vocabulary pronunciation tips, and hashtags strictly following the creator instructions.",
        "Output ONLY a machine-readable payload inside <payload>...</payload>. If returning JSON, format with keys `caption` (str), `hashtags` (list of str), and `markdown_content` (str). Do not include conversational filler outside <payload> tags."
    ]
    if instructions_text:
        playbook_sections.append(f"--- CREATOR INSTRUCTIONS ---\n{instructions_text}")

    state_sections = [
        f"Topic / Word: `{topic}`",
        f"Project Directory: `{project_dir}`",
        f"Output Directory: `{output_dir}`",
        f"Target Copy MD Path: `{copy_path}`",
        f"Target Copy JSON Path: `{copy_json_path}`"
    ]
    if human_feedback and is_revision:
        state_sections.append(f"--- HUMAN REVISION INSTRUCTIONS FOR COPY ---\n{human_feedback}")

    task_instructions = (
        f"Draft the publication social copy and vocabulary tips for '{topic}'.\n"
        f"Output the draft strictly inside <payload>...</payload>."
    )

    return (
        f"<playbook>\n" + "\n\n".join(playbook_sections) + f"\n</playbook>\n\n"
        f"<current_state>\n" + "\n".join(state_sections) + f"\n</current_state>\n\n"
        f"<assigned_task>\n{task_instructions}\n</assigned_task>"
    )
