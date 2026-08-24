from typing import Optional


def build_summarization_prompt(
    transcript: str,
    previous_summary: str = "",
    max_summary_tokens: int = 1000
) -> str:
    """
    Builds a structured <playbook>, <current_state>, <assigned_task> prompt
    for the graph-worker agent to distill earlier conversation turns into
    a high-density, machine-readable summary.
    """
    playbook_sections = [
        "You are the Backend Execution Node acting as a high-density Conversation Summarizer.",
        "Your objective is to compress earlier conversation history while preserving critical context, decisions, tool outcomes, and user constraints.",
        "Rules:",
        "1. Extract key user goals, constraints, preferences, and explicit instructions.",
        "2. Record executed tools, key factual discoveries, created artifacts, and errors encountered.",
        "3. Preserve active tasks, pending actions, and next steps.",
        "4. Omit pleasantries, redundant conversational filler, and temporary scratchpad chatter.",
        f"5. Keep the generated summary concise and under {max_summary_tokens} tokens.",
        "6. Output ONLY the machine-readable XML payload wrapped in <payload>...</payload> without any conversational commentary outside the tags."
    ]

    state_sections = []
    if previous_summary:
        state_sections.append(f"--- EXISTING CUMULATIVE SUMMARY ---\n{previous_summary.strip()}")

    state_sections.append(f"--- CONVERSATION TURNS TO SUMMARIZE ---\n{transcript.strip()}")

    task_instructions = (
        "Distill the conversation turns and combine them with any existing summary into an updated, structured summary.\n\n"
        "MANDATORY OUTPUT FORMAT:\n"
        "You MUST format your entire response within <payload>...</payload> using the following exact XML schema:\n"
        "<payload>\n"
        "<status>success</status>\n"
        "<summary>\n"
        "{distilled_concise_summary_markdown}\n"
        "</summary>\n"
        "</payload>"
    )

    return (
        f"<playbook>\n" + "\n".join(playbook_sections) + f"\n</playbook>\n\n"
        f"<current_state>\n" + "\n\n".join(state_sections) + f"\n</current_state>\n\n"
        f"<assigned_task>\n{task_instructions}\n</assigned_task>"
    )
