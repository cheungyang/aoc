from langgraph.graph import StateGraph, START, END

from typing_extensions import TypedDict

from typing import List, Dict, Any

class AssetIdeationState(TypedDict, total=False):
    image_path: str
    image_prompt: str
    video_plot_path: str
    video_plot_content: str
    video_plot_attempts: int
    max_video_plot_reviews: int
    video_plot_qc_passed: bool
    video_plot_feedback: str
    gate1_decision: str
    clarification_question: str
    source_audio_path: str
    audio_path: str
    overlay_text: List[str]



def create_ideation_subgraph(checkpointer=None):
    from graphs.content_creation.graph import ContentCreationState
    from graphs.content_creation.nodes import (
        setup_and_generate_image_node,
        draft_video_plot_node,
        audit_video_plot_node,
        hitl_image_and_plot_approval_node,
        process_gate1_feedback_node,
        clarify_gate1_node
    )
    from graphs.content_creation.routers import (
        should_continue_setup,
        should_continue_video_plot_audit,
        should_continue_hitl_gate_1
    )
    workflow = StateGraph(ContentCreationState)
    workflow.add_node("setup_and_generate_image", setup_and_generate_image_node)
    workflow.add_node("draft_video_plot", draft_video_plot_node)
    workflow.add_node("audit_video_plot", audit_video_plot_node)
    workflow.add_node("hitl_image_and_plot_approval", hitl_image_and_plot_approval_node)
    workflow.add_node("process_gate1_feedback", process_gate1_feedback_node)
    workflow.add_node("clarify_gate1", clarify_gate1_node)

    workflow.add_edge(START, "setup_and_generate_image")
    workflow.add_conditional_edges(
        "setup_and_generate_image",
        should_continue_setup,
        {"draft_video_plot": "draft_video_plot", END: END}
    )
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
            END: END, # When approved, exit ideation
            "setup_and_generate_image": "setup_and_generate_image",
            "draft_video_plot": "draft_video_plot",
            "clarify_gate1": "clarify_gate1"
        }
    )
    workflow.add_edge("clarify_gate1", "hitl_image_and_plot_approval")
    return workflow.compile(checkpointer=checkpointer, interrupt_after=["hitl_image_and_plot_approval"])

