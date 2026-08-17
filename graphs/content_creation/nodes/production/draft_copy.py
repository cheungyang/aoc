import os
import json
from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.schemas import FinalCopy

async def draft_copy_task(state: dict) -> dict:
    """Drafts social copy, vocabulary breakdown, and hashtags, dual-publishing .md and .json."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_project_path(state.get("project_dir", ""))
    output_dir = normalize_project_path(state.get("output_dir") or (os.path.join(project_dir, topic) if project_dir else ""))
    creator_instructions_path = state.get("creator_instructions_path", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")
    human_feedback = state.get("latest_human_feedback")

    existing_copy = _resolve_asset_path(output_dir, topic, "copy", next_version=False)
    needs_copy_revision = (state.get("gate2_decision") == "revise_copy")

    if os.path.exists(existing_copy) and not needs_copy_revision and not human_feedback:
        return {
            "project_dir": project_dir,
            "output_dir": output_dir,
            "copy_path": existing_copy
        }

    if os.path.exists(existing_copy) and (needs_copy_revision or human_feedback):
        copy_path = _resolve_asset_path(output_dir, topic, "copy", next_version=True)
    else:
        copy_path = existing_copy
    copy_json_path = copy_path.replace(".md", ".json")

    instructions_text = ""
    try:
        with open(creator_instructions_path, "r", encoding="utf-8") as f:
            instructions_text = f.read()
    except Exception:
        pass

    prompt = (
        f"You are the Content Creator drafting social media publication copy for the topic '{topic}'.\n"
        f"--- CREATOR INSTRUCTIONS ---\n{instructions_text}\n----------------------------\n"
        f"Draft the engaging social post title, caption, Cantonese/English vocabulary pronunciation tips, and hashtags."
    )
    if human_feedback and state.get("gate2_decision") == "revise_copy":
        prompt += f"\n\nHuman Revision Instructions for Copy:\n{human_feedback}"

    try:
        from core.loaders.agents_loader import AgentsLoader
        from langchain_google_genai import ChatGoogleGenerativeAI

        config = AgentsLoader()._agent_configs.get("content-creator", {})
        model_name = config.get("model", "gemini-3.7-flash")

        llm = ChatGoogleGenerativeAI(model=model_name).with_structured_output(FinalCopy)
        copy_data: FinalCopy = await llm.ainvoke(prompt)

        polished_copy = copy_data.markdown_content

        if copy_path:
            os.makedirs(os.path.dirname(copy_path), exist_ok=True)
            with open(copy_path, "w", encoding="utf-8") as f:
                f.write(polished_copy)
            with open(copy_json_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(copy_data.model_dump(), indent=2))
    except Exception as e:
        print(f"draft_copy_task: Error saving copy to {copy_path}: {e}")
        polished_copy = ""

    _append_execution_log(
        output_dir=output_dir,
        topic=topic,
        actor="📱 Content Creator",
        event_title="Publication Copy Drafted",
        details={
            "Copy MD Path": copy_path,
            "Copy JSON Path": copy_json_path,
            "Preview": polished_copy[:300] + ("..." if len(polished_copy) > 300 else "")
        },
        log_path=execution_log_path
    )

    return {
        "project_dir": project_dir,
        "output_dir": output_dir,
        "copy_path": copy_path
    }
