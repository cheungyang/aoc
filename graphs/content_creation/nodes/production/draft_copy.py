import os
import re
import json
from graphs.content_creation.utils.paths import normalize_path, canonicalize_output_dir, resolve_task_asset, load_project_context
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate2_intent

async def draft_copy_task(state: dict) -> dict:
    """Drafts social copy, vocabulary breakdown, and hashtags, dual-publishing .md and .json."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_dir = normalize_path(state.get("project_dir", ""))
    output_dir = canonicalize_output_dir(project_dir, state.get("output_dir"), topic)
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_dir, "execution_log.md") if output_dir else "")
    human_feedback = state.get("latest_human_feedback")
    gate2_decision = state.get("gate2_decision")
    needs_copy_revision = (gate2_decision == "revise_copy" or bool(human_feedback and gate2_decision == "revise_copy"))

    copy_path, should_generate = resolve_task_asset(output_dir, topic, "copy", needs_revision=needs_copy_revision)
    if not should_generate:
        return {
            "project_dir": project_dir,
            "output_dir": output_dir,
            "copy_path": copy_path
        }

    copy_json_path = copy_path.replace(".md", ".json")

    ctx = load_project_context(
        project_dir=project_dir,
        manifest_path=state.get("manifest_path", ""),
        creator_instructions_path=state.get("creator_instructions_path", "")
    )
    instructions_text = ctx["project_guidelines"]

    prompt = (
        f"You are the Content Creator drafting social media publication copy for the topic '{topic}'.\n"
        f"--- CREATOR INSTRUCTIONS ---\n{instructions_text}\n----------------------------\n"
        f"Draft the engaging social post title, caption, Cantonese/English vocabulary pronunciation tips, and hashtags."
    )
    if human_feedback and gate2_decision == "revise_copy":
        prompt += f"\n\nHuman Revision Instructions for Copy:\n{human_feedback}"

    try:
        from tools.agent_call import agent_call
        import re

        channel = state.get("channel") or "content-creation"
        tool_res = await agent_call.ainvoke({
            "agent_id": "content-creator",
            "prompt": prompt,
            "channel": channel
        })

        payload = ""
        m = re.search(r"<payload>(.*?)</payload>", str(tool_res), re.DOTALL)
        if m:
            payload = m.group(1).strip()
        else:
            payload = str(tool_res).strip()

        polished_copy = payload
        copy_dict = {
            "caption": payload,
            "hashtags": [],
            "markdown_content": payload
        }

        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                copy_dict.update(data)
                polished_copy = data.get("markdown_content") or payload
        except Exception:
            pass

        if copy_path:
            os.makedirs(os.path.dirname(copy_path), exist_ok=True)
            with open(copy_path, "w", encoding="utf-8") as f:
                f.write(polished_copy)
            with open(copy_json_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(copy_dict, indent=2))
    except Exception as e:
        print(f"draft_copy_task: Error executing agent_call for content-creator: {e}")
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
