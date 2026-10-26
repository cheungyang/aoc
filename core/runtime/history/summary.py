"""The summary that stands in for pruned history: its markers, and the version
built without a model call (pruner.py)."""
from typing import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

SUMMARY_PREFIX = "<conversation_summary>"
SUMMARY_SUFFIX = "</conversation_summary>"


def build_heuristic_summary(older_messages: Sequence[BaseMessage], previous_summary: str = "") -> str:
    """
    Builds a high-density, deterministic summary of older conversation turns
    without requiring an active LLM call.
    """
    lines = []
    if previous_summary:
        clean_prev = previous_summary.replace(SUMMARY_PREFIX, "").replace(SUMMARY_SUFFIX, "").strip()
        lines.append(f"Prior Context: {clean_prev}")

    user_intents = []
    actions_taken = []
    key_findings = []

    for msg in older_messages:
        if isinstance(msg, HumanMessage):
            c = msg.content if isinstance(msg.content, str) else str(msg.content)
            clean_c = c.strip().replace("\n", " ")
            if clean_c:
                user_intents.append(clean_c[:200])
        elif isinstance(msg, AIMessage):
            if getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                    if tc_name:
                        actions_taken.append(tc_name)
            c = msg.content if isinstance(msg.content, str) else str(msg.content)
            if c and len(c) > 20 and not getattr(msg, "tool_calls", None):
                key_findings.append(c[:250].strip().replace("\n", " "))
        elif isinstance(msg, ToolMessage):
            name = getattr(msg, "name", "")
            if name and name not in actions_taken:
                actions_taken.append(name)

    summary_parts = []
    if lines:
        summary_parts.extend(lines)

    if user_intents:
        intents_str = " | ".join(user_intents[-4:])  # Keep up to last 4 distinct user requests in older history
        summary_parts.append(f"User Requests / Goals: {intents_str}")

    if actions_taken:
        unique_tools = list(dict.fromkeys(actions_taken))
        summary_parts.append(f"Tools Utilized: {', '.join(unique_tools)}")

    if key_findings:
        findings_str = " | ".join(key_findings[-3:])
        summary_parts.append(f"Key Points / Outcomes: {findings_str}")

    if not summary_parts:
        return "Earlier conversation history condensed."

    return "\n".join(summary_parts)
