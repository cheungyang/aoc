import re
from typing import Dict, Any, Optional
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

# Import state and helper types
from graphs.content_creation.state import (
    ContentCreationState,
    normalize_project_path,
    _resolve_asset_path,
    _append_execution_log,
    _extract_motion_prompt_from_plot
)

# Import execution nodes
from graphs.content_creation.nodes import (
    setup_and_generate_image_node,
    draft_video_plot_node,
    audit_video_plot_node,
    generate_visual_plate_node,
    extract_and_qc_frames_node,
    draft_and_save_copy_node
)

# Import HITL nodes, classifiers, and routers
from graphs.content_creation.hitl import (
    classify_gate1_intent,
    classify_gate2_intent,
    format_gate1_presentation,
    format_gate2_presentation,
    hitl_image_and_plot_approval_node,
    process_gate1_feedback_node,
    clarify_gate1_node,
    hitl_final_package_approval_node,
    process_gate2_feedback_node,
    clarify_gate2_node,
    should_continue_video_plot_audit,
    should_continue_hitl_gate_1,
    should_continue_video_qc,
    should_continue_hitl_gate_2
)


# ==========================================
# Graph Compilation
# ==========================================
def create_graph(checkpointer=None, **kwargs):
    """Compiles and returns the generic instruction-driven content-creation graph with Sqlite checkpointer & 2 HITL gates."""
    if checkpointer is None:
        try:
            from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
            checkpointer = SqliteCheckpointer()
        except Exception:
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()

    workflow = StateGraph(ContentCreationState)

    # 1. Add nodes
    workflow.add_node("setup_and_generate_image", setup_and_generate_image_node)
    workflow.add_node("draft_video_plot", draft_video_plot_node)
    workflow.add_node("audit_video_plot", audit_video_plot_node)
    workflow.add_node("hitl_image_and_plot_approval", hitl_image_and_plot_approval_node)
    workflow.add_node("process_gate1_feedback", process_gate1_feedback_node)
    workflow.add_node("clarify_gate1", clarify_gate1_node)
    workflow.add_node("generate_visual_plate", generate_visual_plate_node)
    workflow.add_node("extract_and_qc_frames", extract_and_qc_frames_node)
    workflow.add_node("draft_and_save_copy", draft_and_save_copy_node)
    workflow.add_node("hitl_final_package_approval", hitl_final_package_approval_node)
    workflow.add_node("process_gate2_feedback", process_gate2_feedback_node)
    workflow.add_node("clarify_gate2", clarify_gate2_node)

    # 2. Add edges & conditional branching
    workflow.add_edge(START, "setup_and_generate_image")
    workflow.add_edge("setup_and_generate_image", "draft_video_plot")
    workflow.add_edge("draft_video_plot", "audit_video_plot")

    workflow.add_conditional_edges(
        "audit_video_plot",
        should_continue_video_plot_audit,
        {
            "setup_and_generate_image": "setup_and_generate_image",
            "draft_video_plot": "draft_video_plot",
            "hitl_image_and_plot_approval": "hitl_image_and_plot_approval"
        }
    )

    workflow.add_edge("hitl_image_and_plot_approval", "process_gate1_feedback")

    workflow.add_conditional_edges(
        "process_gate1_feedback",
        should_continue_hitl_gate_1,
        {
            "generate_visual_plate": "generate_visual_plate",
            "setup_and_generate_image": "setup_and_generate_image",
            "draft_video_plot": "draft_video_plot",
            "clarify_gate1": "clarify_gate1"
        }
    )

    workflow.add_edge("clarify_gate1", "hitl_image_and_plot_approval")

    workflow.add_edge("generate_visual_plate", "extract_and_qc_frames")

    workflow.add_conditional_edges(
        "extract_and_qc_frames",
        should_continue_video_qc,
        {
            "generate_visual_plate": "generate_visual_plate",
            "draft_and_save_copy": "draft_and_save_copy"
        }
    )

    workflow.add_edge("draft_and_save_copy", "hitl_final_package_approval")
    workflow.add_edge("hitl_final_package_approval", "process_gate2_feedback")

    workflow.add_conditional_edges(
        "process_gate2_feedback",
        should_continue_hitl_gate_2,
        {
            END: END,
            "draft_and_save_copy": "draft_and_save_copy",
            "generate_visual_plate": "generate_visual_plate",
            "clarify_gate2": "clarify_gate2"
        }
    )

    workflow.add_edge("clarify_gate2", "hitl_final_package_approval")

    # Compile with interrupt_before at both HITL approval gates
    return workflow.compile(
        checkpointer=checkpointer,
        interrupt_before=["hitl_image_and_plot_approval", "hitl_final_package_approval"]
    )

# Default compiled instance
graph = create_graph()


# ==========================================
# Input / Output Adapters
# ==========================================
def prepare_input(query: str, caller: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """Translates incoming text query / kwargs into initial generic ContentCreationState."""
    if caller and "<caller>" not in query:
        formatted_query = f"<caller>{caller}</caller>\n{query}"
    else:
        formatted_query = query

    project_dir = kwargs.get("project_dir")
    if not project_dir:
        m_pdir = re.search(r'project_dir[:=]\s*["\']?([^"\'\s,]+)["\']?', query, re.IGNORECASE)
        if m_pdir:
            project_dir = m_pdir.group(1).strip()
        else:
            project_dir = ""

    session_id = kwargs.get("session_id")
    thread_id = kwargs.get("thread_id") or session_id

    topic = kwargs.get("topic") or kwargs.get("word")

    # Check if there is an active checkpointer thread with an existing topic or project_dir
    if thread_id:
        try:
            from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
            cp = SqliteCheckpointer()
            snap = cp.get_tuple({"configurable": {"thread_id": thread_id}})
            if snap and snap.checkpoint and "channel_values" in snap.checkpoint:
                ch = snap.checkpoint["channel_values"]
                if not project_dir and ch.get("project_dir"):
                    project_dir = ch["project_dir"]
                if not topic and ch.get("topic"):
                    topic = ch["topic"]
        except Exception:
            pass

    if project_dir:
        project_dir = normalize_project_path(project_dir)

    if not topic:
        m_topic = re.search(r'(?:topic|word)[:=]\s*["\']?([a-zA-Z0-9_ -]+)["\']?', query, re.IGNORECASE)
        if m_topic:
            topic = m_topic.group(1).strip()
        else:
            clean_q = query.replace("<caller>", "").replace("</caller>", "").strip()
            m_create = re.search(
                r'(?:create|generate|make|draft|start)\s+(?:content|video|image|post)?\s*(?:for|about|on)?\s*["\']?([a-zA-Z0-9_-]+)["\']?',
                clean_q,
                re.IGNORECASE
            )
            if m_create and m_create.group(1).lower() not in ["content", "video", "image", "post", "a", "the", "ayla", "me", "this"]:
                topic = m_create.group(1).strip()
            elif clean_q and len(clean_q.split()) <= 2 and not any(w in clean_q.lower() for w in [
                "looking for", "instead of", "change", "revise", "make the", "fix", "update",
                "approved", "looks good", "lgtm", "yes", "no", "proceed", "go ahead"
            ]):
                topic = clean_q
            else:
                topic = "scene"

    # Enforce lowercase for folder/asset uniformity
    topic = str(topic).strip().lower()

    manifest_path = kwargs.get("manifest_path") or (f"{project_dir}/01_Project_Manifest.md" if project_dir else "01_Project_Manifest.md")
    creator_instructions_path = kwargs.get("creator_instructions_path") or (f"{project_dir}/02_Creator_Instructions.md" if project_dir else "02_Creator_Instructions.md")
    qc_playbook_path = kwargs.get("qc_playbook_path") or (f"{project_dir}/03_QC_Playbook.md" if project_dir else "03_QC_Playbook.md")

    output_dir = normalize_project_path(kwargs.get("output_dir") or (f"{project_dir}/words/{topic}" if project_dir else f"words/{topic}"))
    execution_log_path = kwargs.get("execution_log_path") or f"{output_dir}/execution_log.md"

    image_version = kwargs.get("image_version", 1)
    video_plot_version = kwargs.get("video_plot_version", 1)
    video_version = kwargs.get("video_version", 1)
    copy_version = kwargs.get("copy_version", 1)

    image_path = kwargs.get("image_path") or _resolve_asset_path("", output_dir, topic, "image", image_version)
    video_plot_path = kwargs.get("video_plot_path") or _resolve_asset_path("", output_dir, topic, "video_plot", video_plot_version)
    video_path = kwargs.get("video_path") or _resolve_asset_path("", output_dir, topic, "video", video_version)
    copy_path = kwargs.get("copy_path") or _resolve_asset_path("", output_dir, topic, "copy", copy_version)
    qc_timestamps = kwargs.get("qc_timestamps") or [1.0, 2.5, 4.0]

    return {
        "project_dir": project_dir,
        "topic": topic,
        "session_id": session_id,
        "thread_id": thread_id,
        "manifest_path": manifest_path,
        "creator_instructions_path": creator_instructions_path,
        "qc_playbook_path": qc_playbook_path,
        "execution_log_path": execution_log_path,
        "output_dir": output_dir,
        "image_version": image_version,
        "video_plot_version": video_plot_version,
        "video_version": video_version,
        "copy_version": copy_version,
        "image_path": image_path,
        "video_plot_path": video_plot_path,
        "video_path": video_path,
        "copy_path": copy_path,
        "image_prompt": "",
        "video_plot_content": "",
        "video_plot_qc_passed": False,
        "video_plot_feedback": "",
        "extracted_frames": [],
        "qc_timestamps": qc_timestamps,
        "video_qc_passed": False,
        "video_qc_feedback": "",
        "copy_text": "",
        "final_package": {},
        "gate1_decision": "approved",
        "gate2_decision": "approved",
        "latest_human_feedback": "",
        "revision_history": [],
        "clarification_question": "",
        "video_plot_attempts": 0,
        "max_video_plot_reviews": kwargs.get("max_video_plot_reviews", 3),
        "video_qc_attempts": 0,
        "max_video_reviews": kwargs.get("max_video_reviews", 3),
        "messages": [HumanMessage(content=formatted_query)],
        "query": formatted_query,
        "error_message": ""
    }


def format_output(state: Dict[str, Any]) -> str:
    """Extracts final reply text from ContentCreationState reading dynamic versioned asset paths."""
    if isinstance(state, dict):
        if state.get("clarification_question"):
            return state["clarification_question"]
        if state.get("final_package") and "copy_text" in state["final_package"]:
            return format_gate2_presentation(state)
        if state.get("copy_text"):
            return format_gate2_presentation(state)
        if state.get("video_plot_qc_passed") or (state.get("image_path") and state.get("video_plot_content")):
            return format_gate1_presentation(state)
        if state.get("error_message"):
            return f"Content creation failed: {state['error_message']}"
        if "messages" in state and state["messages"]:
            last_msg = state["messages"][-1]
            if hasattr(last_msg, "content"):
                return last_msg.content
            elif isinstance(last_msg, dict) and "content" in last_msg:
                return last_msg["content"]
            return str(last_msg)
    return str(state)
