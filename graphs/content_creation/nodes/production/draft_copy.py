import os
import re
import json
from graphs.content_creation.utils.paths import resolve_task_asset, load_project_context
from graphs.content_creation.utils.logging import _append_execution_log
from graphs.content_creation.utils.classifiers import classify_gate2_intent
from graphs.content_creation.prompts import build_draft_copy_prompt

async def draft_copy_task(state: dict) -> dict:
    """Drafts social copy, vocabulary breakdown, and hashtags, dual-publishing .md and .json."""
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_path = state.get("project_path", "")
    output_path = state.get("output_path", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_path, "execution_log.md") if output_path else "")
    human_feedback = state.get("latest_human_feedback")
    gate2_decision = state.get("gate2_decision")
    needs_copy_revision = (gate2_decision == "revise_copy" or bool(human_feedback and gate2_decision == "revise_copy"))

    copy_path, should_generate = resolve_task_asset(output_path, topic, "copy", needs_revision=needs_copy_revision)
    if not should_generate:
        return {
            "project_path": project_path,
            "output_path": output_path,
            "copy_path": copy_path
        }

    copy_json_path = copy_path.replace(".md", ".json")

    ctx = load_project_context(
        project_path=project_path,
        manifest_path=state.get("manifest_path", ""),
        creator_instructions_path=state.get("creator_instructions_path", "")
    )
    instructions_text = ctx["project_guidelines"]

    prompt = build_draft_copy_prompt(
        topic=topic,
        project_path=project_path,
        output_path=output_path,
        copy_path=copy_path,
        copy_json_path=copy_json_path,
        instructions_text=instructions_text or "",
        human_feedback=human_feedback or "",
        is_revision=bool(gate2_decision == "revise_copy")
    )

    try:
        from tools.agent_call import agent_call
        import re

        channel = state.get("channel") or "content-creation"
        tool_res = await agent_call.ainvoke({
            "agent_id": "graph-worker",
            "prompt": prompt,
            "channel": channel
        })

        payload = ""
        m = re.search(r"<payload>(.*?)</payload>", str(tool_res), re.DOTALL)
        if m:
            payload = m.group(1).strip()
        else:
            payload = str(tool_res).strip()

        # 1. Direct XML tag extraction for zero-overhead, certain parsing
        status_m = re.search(r"<status>(.*?)</status>", payload, re.DOTALL)
        caption_m = re.search(r"<caption_text>(.*?)</caption_text>", payload, re.DOTALL)
        hashtags_m = re.search(r"<hashtags>(.*?)</hashtags>", payload, re.DOTALL)
        vocab_m = re.search(r"<vocabulary>(.*?)</vocabulary>", payload, re.DOTALL)
        md_m = re.search(r"<markdown_content>(.*?)</markdown_content>", payload, re.DOTALL)

        if md_m or caption_m:
            caption_val = caption_m.group(1).strip() if caption_m else ""
            vocab_val = vocab_m.group(1).strip() if vocab_m else ""
            tags_list = []
            if hashtags_m:
                raw_tags = hashtags_m.group(1).strip()
                tags_list = [t.strip() for t in re.split(r'[\s,]+', raw_tags) if t.strip()]

            if md_m:
                polished_copy = md_m.group(1).strip()
            elif os.path.isfile(copy_path):
                try:
                    with open(copy_path, "r", encoding="utf-8") as f:
                        polished_copy = f.read()
                except Exception:
                    polished_copy = ""
            else:
                sections = []
                if caption_val:
                    sections.append(f"**Caption:** {caption_val}")
                if vocab_val:
                    sections.append(f"**Vocabulary:** {vocab_val}")
                if tags_list:
                    sections.append(f"**Hashtags:** {' '.join(tags_list)}")
                polished_copy = "\n\n".join(sections) if sections else payload

            copy_dict = {
                "caption": caption_val or payload,
                "hashtags": tags_list,
                "vocabulary": vocab_val,
                "markdown_content": polished_copy
            }
        else:
            # 2. Fallback: JSON parsing
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
        print(f"draft_copy_task: Error executing agent_call for graph-worker: {e}")
        polished_copy = ""

    _append_execution_log(
        output_path=output_path,
        topic=topic,
        actor="⚙️ Graph Worker (Copywriting)",
        event_title="Publication Copy Drafted",
        details={
            "Copy MD Path": copy_path,
            "Copy JSON Path": copy_json_path,
            "Preview": polished_copy[:300] + ("..." if len(polished_copy) > 300 else "")
        },
        log_path=execution_log_path
    )

    return {
        "project_path": project_path,
        "output_path": output_path,
        "copy_path": copy_path
    }
