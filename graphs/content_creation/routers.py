from langgraph.graph import END

def should_continue_setup(state: dict):
    if state.get("error_message"):
        return END
    return "draft_video_plot"

def should_continue_video_plot_audit(state: dict):
    if state.get("video_plot_qc_passed"):
        return "hitl_image_and_plot_approval"
    target = state.get("video_plot_feedback", "")
    if "TARGET: IMAGE" in target:
        return "setup_and_generate_image"
    return "draft_video_plot"

def should_continue_hitl_gate_1(state: dict):
    decision = state.get("gate1_decision", "approved")
    if decision == "approved":
        return END
    elif decision == "revise_image":
        return "setup_and_generate_image"
    elif decision == "revise_plot":
        return "draft_video_plot"
    elif decision == "clarify":
        return "clarify_gate1"
    return END

def should_continue_video_qc(state: dict):
    if state.get("video_qc_passed"):
        return END
    if state.get("video_qc_attempts", 0) >= state.get("max_video_reviews", 3):
        return "hitl_video_qc_failure_intervention"
    target = state.get("video_qc_rejection_target", "visual_plate")
    if target in ["remix", "audio_text", "text", "audio"]:
        return "remix_video"
    return "generate_visual_plate"

def should_continue_hitl_gate_2(state: dict):
    decision = state.get("gate2_decision", "approved")
    if decision == "approved":
        return END
    elif decision == "revise_copy":
        return "draft_and_save_copy"
    elif decision == "revise_remix":
        return "remix_video"
    elif decision == "revise_video":
        return "generate_visual_plate"
    elif decision == "clarify":
        return "clarify_gate2"
    return END
