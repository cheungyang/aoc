import os
from graphs.content_creation.utils.classifiers import classify_gate1_intent, classify_gate2_intent, extract_remix_parameters
from graphs.content_creation.utils.logging import _append_execution_log


async def process_gate1_node(state: dict) -> dict:
    """
    Explicit Gate 1 Processor Node:
    Extracts raw human feedback received at Gate 1 interrupt, classifies the exact intent,
    records the decision into the graph state channel, and audits the transition.
    """
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_path = state.get("project_path", "")
    output_path = state.get("output_path", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_path, "execution_log.md") if output_path else "")

    human_feedback = state.get("latest_human_feedback") or ""
    decision = classify_gate1_intent(human_feedback) if human_feedback else state.get("gate1_decision", "approved")

    _append_execution_log(
        output_path=output_path,
        topic=topic,
        actor="🚦 Gate 1 Processor",
        event_title="Gate 1 Human Intent Processed",
        details={
            "Human Feedback": human_feedback or "(None / Auto-Proceed)",
            "Classified Decision": decision.upper(),
            "Next Destination": "ideate_package" if decision in ["revise_image", "revise_plot"] else "produce_deliverables"
        },
        log_path=execution_log_path
    )

    return {
        "gate1_decision": decision
    }


async def process_gate2_node(state: dict) -> dict:
    """
    Explicit Gate 2 Processor Node:
    Extracts raw human feedback received at Gate 2 interrupt, classifies the exact intent,
    records the decision into the graph state channel, and audits the transition.
    """
    if state.get("error_message"):
        return {}

    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    project_path = state.get("project_path", "")
    output_path = state.get("output_path", "")
    execution_log_path = state.get("execution_log_path") or (os.path.join(output_path, "execution_log.md") if output_path else "")

    human_feedback = state.get("latest_human_feedback") or ""
    decision = classify_gate2_intent(human_feedback) if human_feedback else state.get("gate2_decision", "approved")
    remix_params = extract_remix_parameters(human_feedback) if human_feedback else {}

    _append_execution_log(
        output_path=output_path,
        topic=topic,
        actor="🚦 Gate 2 Processor",
        event_title="Gate 2 Human Intent Processed",
        details={
            "Human Feedback": human_feedback or "(None / Auto-Proceed)",
            "Classified Decision": decision.upper(),
            "Next Destination": "produce_deliverables" if decision in ["revise_copy", "revise_video", "revise_remix"] else "COMPLETED"
        },
        log_path=execution_log_path
    )

    return {
        "gate2_decision": decision,
        "remix_params": remix_params
    }
