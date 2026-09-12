"""Gate processors: the graph's single human-intent decision site.

Every message from the human is parsed here and nowhere else. The eight
scattered ``classify_gate*_intent`` calls that used to live inside the task
modules are gone: a task now reads the decision the gate recorded, it does not
re-derive it from the raw text and reach a different conclusion.

The rule these nodes enforce:

    an unrecognised message must never reach a node that can spend money.

An unclear message produces a ``pending_notice`` — a clarification card — and
the macro node short-circuits on it, re-presenting the gate without
regenerating anything.
"""

import os

from graphs.content_creation.adapters import format_clarification_card, format_status_card
from graphs.content_creation.utils.classifiers import (
    ABORT,
    APPROVE,
    GATE1,
    GATE2,
    RETRY,
    REVISE,
    STATUS,
    UNCLEAR,
    looks_like_instruction,
    parse_intent,
)
from graphs.content_creation.utils.logging import _append_execution_log


def _cleared() -> dict:
    """State channels that must be reset whenever a decision is taken.

    ``pending_notice`` gates the macro-node short circuit and ``pending_feedback``
    holds the text of an earlier unclear message. Leaving either set after a
    clear decision would re-present the gate forever.
    """
    return {"pending_notice": "", "pending_feedback": ""}


async def _process_gate(state: dict, gate: str) -> dict:
    topic = str(state.get("topic") or state.get("word") or "scene").strip().lower()
    output_path = state.get("output_path", "")
    execution_log_path = state.get("execution_log_path") or (
        os.path.join(output_path, "execution_log.md") if output_path else ""
    )
    decision_channel = "gate1_decision" if gate == GATE1 else "gate2_decision"

    human_feedback = (state.get("latest_human_feedback") or "").strip()
    pending_feedback = state.get("pending_feedback") or ""

    # No message at all: this is a resume with nothing to decide, so preserve
    # whatever decision is already recorded. Pre-existing behaviour, kept
    # deliberately -- changing what "silence" means is not this change.
    if not human_feedback:
        if state.get("error_message"):
            return {}
        return {decision_channel: state.get(decision_channel, "approved")}

    intent = parse_intent(human_feedback, gate=gate, pending_instruction=pending_feedback)

    # `retry` is the one intent allowed to run while the graph is in an error
    # state -- it exists precisely to clear it. Every other intent defers to
    # the error, which the human has already been shown.
    if state.get("error_message") and intent.action != RETRY:
        return {}

    update: dict = {decision_channel: intent.decision(gate)}
    destination = ""

    if intent.action == UNCLEAR:
        # Keep the human's own wording so answering with an ordinal does not
        # lose it -- but let a newer instruction supersede an older one.
        instruction = (
            human_feedback if looks_like_instruction(human_feedback) else pending_feedback
        )
        update.update(
            {
                "pending_notice": format_clarification_card(
                    gate=gate,
                    message=human_feedback,
                    reason=intent.reason,
                    has_pending=bool(instruction),
                ),
                "pending_feedback": instruction,
            }
        )
        destination = "re-present gate (no work)"

    elif intent.action == STATUS:
        update.update(
            {
                "pending_notice": format_status_card(state, gate=gate),
                "pending_feedback": pending_feedback,
            }
        )
        destination = "re-present gate (no work)"

    elif intent.action == ABORT:
        update.update(_cleared())
        destination = "END"

    elif intent.action == RETRY:
        # Clearing the error is what makes "reply retry" mean something. Until
        # now the error channel was sticky and every node short-circuited on
        # it, so the retry the error message advertised could never run.
        update.update(_cleared())
        update.update({"error_message": "", "quota_exceeded": False})
        destination = "re-run producing node"

    elif intent.action == REVISE:
        update.update(_cleared())
        update["latest_human_feedback"] = intent.instruction
        if intent.params:
            update["remix_params"] = {**(state.get("remix_params") or {}), **intent.params}
        destination = "ideate_package" if gate == GATE1 else "produce_deliverables"

    elif intent.action == APPROVE:
        update.update(_cleared())
        destination = "produce_deliverables" if gate == GATE1 else "COMPLETED"

    _append_execution_log(
        output_path=output_path,
        topic=topic,
        actor="🚦 Gate 1 Processor" if gate == GATE1 else "🚦 Gate 2 Processor",
        event_title=f"{'Gate 1' if gate == GATE1 else 'Gate 2'} Human Intent Processed",
        details={
            "Human Feedback": human_feedback,
            "Parsed Action": intent.action.upper(),
            "Target": intent.target or "—",
            "Classified Decision": str(update.get(decision_channel, "")).upper(),
            "Next Destination": destination,
            "Spends": "yes" if intent.may_spend else "no",
        },
        log_path=execution_log_path,
    )

    return update


async def process_gate1_node(state: dict) -> dict:
    """Parses the human's Gate 1 reply into a routable decision."""
    return await _process_gate(state, GATE1)


async def process_gate2_node(state: dict) -> dict:
    """Parses the human's Gate 2 reply into a routable decision."""
    return await _process_gate(state, GATE2)
