from langgraph.graph import StateGraph, START, END

from typing_extensions import TypedDict, List, Dict, Any, Literal

class VideoProductionState(TypedDict, total=False):
    image_path: str
    raw_video_path: str
    video_path: str
    audio_path: str
    overlay_text: List[str]
    remix_actions: List[Dict[str, Any]]
    audio_verified: bool
    extracted_frames: List[str]
    qc_timestamps: List[float]
    video_qc_passed: bool
    video_qc_feedback: str
    video_qc_rejection_target: Literal["visual_plate", "remix", "both"]
    video_persisted: bool
    video_generation_error: str
    video_qc_attempts: int
    failed_node: str
    debugger_attempts: int
    max_video_reviews: int


from typing import TypedDict, List, Dict, Any, Literal


def create_video_production_subgraph(checkpointer=None):
    from graphs.content_creation.graph import ContentCreationState
    from graphs.content_creation.nodes import (
        generate_visual_plate_node,
        remix_video_node,
        extract_and_qc_frames_node,
        evaluate_video_qc_node,
        fail_fast_video_qc_node,
        autonomous_debugger_node
    )
    from graphs.content_creation.routers import should_continue_video_qc
    workflow = StateGraph(ContentCreationState)
    workflow.add_node("generate_visual_plate", generate_visual_plate_node)
    workflow.add_node("remix_video", remix_video_node)
    workflow.add_node("extract_and_qc_frames", extract_and_qc_frames_node)
    workflow.add_node("evaluate_video_qc", evaluate_video_qc_node)
    workflow.add_node("fail_fast_video_qc", fail_fast_video_qc_node)
    workflow.add_node("autonomous_debugger", autonomous_debugger_node)

    def route_on_error(state: ContentCreationState):
        return "error" if state.get("error_message") else "ok"
        
    def after_debugger(state: ContentCreationState):
        if state.get("error_message"):
            return END
        return state.get("failed_node", END)

    workflow.add_edge(START, "generate_visual_plate")
    
    workflow.add_conditional_edges("generate_visual_plate", route_on_error, {
        "error": "autonomous_debugger",
        "ok": "remix_video"
    })
    
    workflow.add_conditional_edges("remix_video", route_on_error, {
        "error": "autonomous_debugger",
        "ok": "extract_and_qc_frames"
    })
    
    workflow.add_conditional_edges("extract_and_qc_frames", route_on_error, {
        "error": "autonomous_debugger",
        "ok": "evaluate_video_qc"
    })
    
    workflow.add_conditional_edges(
        "evaluate_video_qc",
        should_continue_video_qc,
        {
            END: END,
            "generate_visual_plate": "generate_visual_plate",
            "remix_video": "remix_video",
            "hitl_video_qc_failure_intervention": "fail_fast_video_qc"
        }
    )
    workflow.add_edge("fail_fast_video_qc", END)
    
    workflow.add_conditional_edges("autonomous_debugger", after_debugger, {
        END: END,
        "generate_visual_plate": "generate_visual_plate",
        "remix_video": "remix_video",
        "extract_and_qc_frames": "extract_and_qc_frames"
    })
    
    return workflow.compile(checkpointer=checkpointer)

