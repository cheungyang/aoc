import os
import re
from typing import Dict, Any, Optional
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

# Import state and helper types
from typing import Dict, Any, List
from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage

from graphs.content_creation.subgraph_ideation import AssetIdeationState
from graphs.content_creation.subgraph_video import VideoProductionState
from graphs.content_creation.subgraph_copywriting import CopywritingState

class OverallProjectState(TypedDict, total=False):
    project_dir: str
    topic: str
    session_id: str
    thread_id: str
    manifest_path: str
    creator_instructions_path: str
    qc_playbook_path: str
    execution_log_path: str
    output_dir: str
    messages: List[AnyMessage]
    query: str
    error_message: str
    latest_human_feedback: str
    final_package: Dict[str, Any]
    source_audio_path: str

class ContentCreationState(OverallProjectState, AssetIdeationState, VideoProductionState, CopywritingState):
    revision_history: List[Dict[str, Any]]

from graphs.content_creation.utils.paths import normalize_project_path, _resolve_asset_path

# Import execution nodes
from graphs.content_creation.nodes import (
    setup_and_generate_image_node,
    draft_video_plot_node,
    audit_video_plot_node,
    generate_visual_plate_node,
    remix_video_node,
    extract_and_qc_frames_node,
    evaluate_video_qc_node,
    draft_and_save_copy_node,
    ask_for_audio_node,
    receive_audio_node
)

# Import HITL nodes, classifiers, and routers
from graphs.content_creation.nodes import (
    hitl_final_package_approval_node,
    process_gate2_feedback_node,
    clarify_gate2_node
)
from graphs.content_creation.routers import (
    should_continue_setup,
    should_continue_video_plot_audit,
    should_continue_hitl_gate_1,
    should_continue_video_qc,
    should_continue_hitl_gate_2
)


# ==========================================
# Graph Compilation
# ==========================================
from graphs.content_creation.subgraph_ideation import create_ideation_subgraph
from graphs.content_creation.subgraph_video import create_video_production_subgraph
from graphs.content_creation.subgraph_copywriting import create_copywriting_subgraph

from graphs.content_creation.adapters import prepare_input, format_output


def create_graph(checkpointer=None, **kwargs):
    """Compiles the master content-creation graph orchestrating parallel sub-graphs."""
    if checkpointer is None:
        try:
            from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
            checkpointer = SqliteCheckpointer()
        except Exception:
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()

    ideation_sg = create_ideation_subgraph()
    video_sg = create_video_production_subgraph()
    copy_sg = create_copywriting_subgraph()

    workflow = StateGraph(ContentCreationState)

    # Add the sub-graphs as adapter nodes for STRICT state isolation
    async def run_ideation(state: dict):
        keys = ["project_dir", "topic", "output_dir", "manifest_path", "creator_instructions_path", "qc_playbook_path", "error_message", "image_path", "image_prompt", "video_plot_path", "video_plot_content", "video_plot_attempts", "max_video_plot_reviews", "video_plot_qc_passed", "video_plot_feedback", "gate1_decision", "clarification_question", "latest_human_feedback", "source_audio_path", "audio_path", "overlay_text"]
        subset = {k: state.get(k) for k in keys if k in state}
        return await ideation_sg.ainvoke(subset)

    async def run_video(state: dict):
        keys = ["project_dir", "topic", "output_dir", "manifest_path", "creator_instructions_path", "qc_playbook_path", "error_message", "raw_video_path", "video_path", "audio_path", "overlay_text", "remix_actions", "audio_verified", "extracted_frames", "qc_timestamps", "video_qc_passed", "video_qc_feedback", "video_qc_rejection_target", "video_persisted", "video_generation_error", "video_qc_attempts", "failed_node", "debugger_attempts", "max_video_reviews", "video_plot_content"]
        subset = {k: state.get(k) for k in keys if k in state}
        return await video_sg.ainvoke(subset)
        
    async def run_copy(state: dict):
        keys = ["project_dir", "topic", "output_dir", "manifest_path", "creator_instructions_path", "qc_playbook_path", "error_message", "copy_path", "copy_text", "gate2_decision", "latest_human_feedback"]
        subset = {k: state.get(k) for k in keys if k in state}
        return await copy_sg.ainvoke(subset)

    workflow.add_node("ideation", run_ideation)
    workflow.add_node("video_production", run_video)
    workflow.add_node("copywriting", run_copy)

    # Add the final gate node and its routers
    workflow.add_node("hitl_final_package_approval", hitl_final_package_approval_node)
    workflow.add_node("process_gate2_feedback", process_gate2_feedback_node)
    workflow.add_node("clarify_gate2", clarify_gate2_node)

    # Audio input at the very start
    workflow.add_node("ask_for_audio", ask_for_audio_node)
    workflow.add_node("receive_audio", receive_audio_node)
    
    workflow.add_edge(START, "ask_for_audio")
    workflow.add_edge("ask_for_audio", "receive_audio")
    
    def check_audio_router(state: ContentCreationState):
        if state.get("source_audio_path"):
            return "ideation"
        return "ask_for_audio"
        
    workflow.add_conditional_edges("receive_audio", check_audio_router, ["ask_for_audio", "ideation"])

    def after_ideation_router(state: ContentCreationState):
        if state.get("error_message") or state.get("gate1_decision") != "approved":
            return END
        # Once ideation is done, fan-out to parallel production of video and copy
        return ["video_production", "copywriting"]
    
    workflow.add_conditional_edges("ideation", after_ideation_router, ["video_production", "copywriting", END])

    # Fan-in from parallel branches
    def wait_for_both(state: ContentCreationState):
        # LangGraph automatically handles joining parallel branches if they both route to the same node
        return "hitl_final_package_approval"

    workflow.add_edge("video_production", "hitl_final_package_approval")
    workflow.add_edge("copywriting", "hitl_final_package_approval")

    workflow.add_edge("hitl_final_package_approval", "process_gate2_feedback")
    
    # Gate 2 Feedback routing
    def master_feedback_router(state: ContentCreationState):
        decision = state.get("gate2_decision")
        if decision == "approved":
            return END
        elif decision == "revise_copy":
            return "copywriting"
        elif decision == "revise_video":
            return "video_production"
        elif decision == "clarify":
            return "clarify_gate2"
        return END

    workflow.add_conditional_edges("process_gate2_feedback", master_feedback_router)
    workflow.add_edge("clarify_gate2", "hitl_final_package_approval")

    return workflow.compile(
        checkpointer=checkpointer,
        interrupt_after=["hitl_final_package_approval", "ask_for_audio"]
    )

# Default compiled instance
graph = create_graph()


