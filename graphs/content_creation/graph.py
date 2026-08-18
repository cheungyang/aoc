import os
from typing import Dict, Any, Optional, List
from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage
from langgraph.graph import StateGraph, START, END

# Lean & Unified State Schema
class ContentCreationState(TypedDict, total=False):
    # 1. Project Context & Guidelines
    project_dir: str
    output_dir: str
    topic: str
    style: str
    session_id: str
    thread_id: str
    messages: List[AnyMessage]
    error_message: str
    manifest_path: str
    creator_instructions_path: str
    qc_playbook_path: str
    execution_log_path: str

    # 2. Asset File Inputs & Deliverable Paths
    source_audio_path: str
    overlay_text: str
    image_path: str
    video_plot_path: str
    raw_video_path: str
    remixed_video_path: str
    extracted_frames_path: List[str]
    copy_path: str

    # 3. Execution Flags & HITL Decision Routing
    video_plot_qc_passed: bool
    video_qc_passed: bool
    video_qc_attempts: int
    video_qc_feedback: str
    gate1_decision: str
    gate2_decision: str
    latest_human_feedback: str
    final_package: Dict[str, Any]

# Import the 3 Macro Nodes
from graphs.content_creation.nodes.ingestion.ingest_audio_node import ingest_audio_node, ask_for_audio_node
from graphs.content_creation.nodes.ideation.ideate_package_node import ideate_package_node
from graphs.content_creation.nodes.production.produce_deliverables_node import produce_deliverables_node

from graphs.content_creation.utils.classifiers import classify_gate1_intent, classify_gate2_intent
from graphs.content_creation.adapters import prepare_input, format_output


def create_graph(checkpointer=None, **kwargs):
    """Compiles the 3-macro-node content creation StateGraph."""
    if checkpointer is None:
        try:
            from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
            checkpointer = SqliteCheckpointer()
        except Exception:
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()

    workflow = StateGraph(ContentCreationState)

    # 1. Register the Macro Nodes
    workflow.add_node("ingest_audio", ingest_audio_node)
    workflow.add_node("ask_for_audio", ask_for_audio_node)
    workflow.add_node("ideate_package", ideate_package_node)
    workflow.add_node("produce_deliverables", produce_deliverables_node)

    # 2. Graph Wiring
    workflow.add_edge(START, "ingest_audio")

    def check_audio_router(state: ContentCreationState):
        if state.get("source_audio_path"):
            return "ideate_package"
        return "ask_for_audio"

    workflow.add_conditional_edges(
        "ingest_audio",
        check_audio_router,
        ["ask_for_audio", "ideate_package"]
    )
    workflow.add_edge("ask_for_audio", "ingest_audio")

    # HITL Gate 1 Router
    def gate1_router(state: ContentCreationState):
        if state.get("error_message"):
            return END
        feedback = state.get("latest_human_feedback") or ""
        decision = classify_gate1_intent(feedback) if feedback else state.get("gate1_decision", "approved")
        if decision in ["revise_image", "revise_plot"]:
            return "ideate_package"
        return "produce_deliverables"

    workflow.add_conditional_edges(
        "ideate_package",
        gate1_router,
        ["ideate_package", "produce_deliverables", END]
    )

    # HITL Gate 2 Router (supports revise_video, revise_remix, and revise_copy)
    def gate2_router(state: ContentCreationState):
        if state.get("error_message"):
            return END
        feedback = state.get("latest_human_feedback") or ""
        decision = classify_gate2_intent(feedback) if feedback else state.get("gate2_decision", "approved")
        if decision in ["revise_copy", "revise_video", "revise_remix"]:
            return "produce_deliverables"
        return END

    workflow.add_conditional_edges(
        "produce_deliverables",
        gate2_router,
        ["produce_deliverables", END]
    )

    return workflow.compile(
        checkpointer=checkpointer,
        interrupt_after=["ask_for_audio", "ideate_package", "produce_deliverables"]
    )

# Default compiled instance
graph = create_graph()
