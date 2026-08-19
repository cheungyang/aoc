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
    """Builds structured <playbook>, <current_state>, <assigned_task> prompt for graph-worker copywriting with strict XML output format."""
    playbook_sections = [
        f"You are the Content Creator drafting social media publication copy for the topic '{topic}'.",
        "Draft engaging social post title, caption, Cantonese/English vocabulary pronunciation tips, and hashtags strictly following the creator instructions.",
        "Output ONLY a machine-readable XML payload wrapped in <payload>...</payload> adhering strictly to the schema below without any conversational text outside the tags."
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
        f"Draft the publication social copy and vocabulary tips for '{topic}'.\n\n"
        "MANDATORY OUTPUT FORMAT:\n"
        "You MUST format your entire response within <payload>...</payload> using the following exact XML schema (fill each tag with your generated result):\n"
        "<payload>\n"
        "<status>{success|error}</status>\n"
        "<error>{error_details_if_any_else_empty}</error>\n"
        "<copy_path>{copy_path}</copy_path>\n"
        "<caption_text>{engaging_caption_content}</caption_text>\n"
        "<hashtags>{space_separated_hashtags}</hashtags>\n"
        "<vocabulary>{vocabulary_pronunciation_notes}</vocabulary>\n"
        "</payload>"
    )

    return (
        f"<playbook>\n" + "\n\n".join(playbook_sections) + f"\n</playbook>\n\n"
        f"<current_state>\n" + "\n".join(state_sections) + f"\n</current_state>\n\n"
        f"<assigned_task>\n{task_instructions}\n</assigned_task>"
    )
