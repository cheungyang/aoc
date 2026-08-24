import json
import re
from typing import List, Sequence, Any, Optional, Tuple, Dict
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)
from core.util.config import Config


SUMMARY_PREFIX = "<conversation_summary>"
SUMMARY_SUFFIX = "</conversation_summary>"


def estimate_message_tokens(msg: Any) -> int:
    """
    Estimates the token count of a single message or dict.
    Uses standard ~4 characters per token heuristic + overhead for tool calls/metadata.
    """
    if msg is None:
        return 0

    tokens = 4  # Baseline message role/framing overhead

    # Extract text content
    content = getattr(msg, "content", msg if isinstance(msg, (str, dict, list)) else "")
    if isinstance(content, str):
        tokens += max(1, len(content) // 4)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, str):
                tokens += max(1, len(item) // 4)
            elif isinstance(item, dict):
                text_val = item.get("text") or item.get("content") or ""
                if text_val:
                    tokens += max(1, len(str(text_val)) // 4)
                # Media/image parts estimate
                if item.get("type") in ("image_url", "image", "media"):
                    tokens += 300
    elif isinstance(content, dict):
        tokens += max(1, len(json.dumps(content)) // 4)

    # Tool calls in AIMessage
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls and isinstance(tool_calls, list):
        for tc in tool_calls:
            tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
            tc_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
            tokens += 10 + max(1, len(str(tc_name)) // 4) + max(1, len(json.dumps(tc_args)) // 4)

    # ToolMessage metadata
    if isinstance(msg, ToolMessage):
        tool_name = getattr(msg, "name", "")
        tc_id = getattr(msg, "tool_call_id", "")
        tokens += 5 + max(1, len(str(tool_name)) // 4) + max(1, len(str(tc_id)) // 4)

    return tokens


def estimate_total_tokens(messages: Sequence[Any]) -> int:
    """Estimates total tokens for a sequence of messages."""
    return sum(estimate_message_tokens(m) for m in messages)


def find_safe_boundary(messages: Sequence[BaseMessage], window_messages: int = 15) -> int:
    """
    Finds a safe split index `split_idx` dividing `messages` into `older = messages[:split_idx]`
    and `recent = messages[split_idx:]`.

    Safety Invariant:
    - Never split an AIMessage(tool_calls=[...]) from its corresponding ToolMessage(s).
    - `recent` must begin on a clean boundary (e.g. HumanMessage or top-level turn).
    - Preserves at least `window_messages` recent messages when possible.
    """
    total_len = len(messages)
    if total_len <= window_messages:
        return 0

    target_idx = max(0, total_len - window_messages)

    # Map out tool call units: start_idx -> end_idx (inclusive of all ToolMessages)
    tool_units: Dict[int, int] = {}
    i = 0
    while i < total_len:
        msg = messages[i]
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            expected_ids = set()
            for tc in msg.tool_calls:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                if tc_id:
                    expected_ids.add(tc_id)

            j = i + 1
            while j < total_len and expected_ids:
                next_msg = messages[j]
                if isinstance(next_msg, ToolMessage):
                    t_id = getattr(next_msg, "tool_call_id", None)
                    if t_id in expected_ids:
                        expected_ids.remove(t_id)
                elif isinstance(next_msg, (HumanMessage, AIMessage)):
                    # Break out if a new turn starts unexpectedly
                    break
                j += 1
            tool_units[i] = j - 1  # end index inclusive
            i = j
        else:
            i += 1

    # Check if target_idx falls inside a tool unit
    for start_idx, end_idx in tool_units.items():
        if start_idx <= target_idx <= end_idx:
            # Shift target_idx to start_idx to keep the entire tool transaction in the recent window
            target_idx = start_idx
            break

    # Check if target_idx points to a ToolMessage (orphaned)
    while target_idx > 0 and isinstance(messages[target_idx], ToolMessage):
        target_idx -= 1

    # If target_idx points to an AIMessage with tool calls, check that all preceding is clean
    if target_idx > 0 and isinstance(messages[target_idx - 1], AIMessage) and getattr(messages[target_idx - 1], "tool_calls", None):
        target_idx -= 1

    # Ideally start recent window on a HumanMessage if one exists nearby (within 3 messages)
    preferred_idx = target_idx
    for lookback in range(target_idx, max(0, target_idx - 3), -1):
        if isinstance(messages[lookback], HumanMessage):
            preferred_idx = lookback
            break

    return preferred_idx


def _format_message_for_summary(msg: BaseMessage) -> str:
    """Formats a single message into a compact text line for summarization."""
    if isinstance(msg, HumanMessage):
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        # Truncate extremely long single human messages (e.g. pasted logs) in summary input
        if len(content) > 1000:
            content = content[:1000] + "... [truncated]"
        return f"User: {content}"
    elif isinstance(msg, AIMessage):
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if len(content) > 1000:
            content = content[:1000] + "... [truncated]"
        parts = []
        if content.strip():
            parts.append(content)
        if getattr(msg, "tool_calls", None):
            tcs = [tc.get("name", "tool") if isinstance(tc, dict) else getattr(tc, "name", "tool") for tc in msg.tool_calls]
            parts.append(f"[Executed tools: {', '.join(tcs)}]")
        return f"Assistant: {' '.join(parts)}"
    elif isinstance(msg, ToolMessage):
        tool_name = getattr(msg, "name", "Tool")
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if len(content) > 300:
            content = content[:300] + "... [output truncated]"
        return f"Tool ({tool_name}): {content}"
    elif isinstance(msg, SystemMessage):
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if SUMMARY_PREFIX in content:
            return ""  # Skip existing summary marker lines in input
        if len(content) > 300:
            content = content[:300] + "... [truncated]"
        return f"System: {content}"
    return f"Message: {str(msg)}"


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


class ContextPruner:
    """
    Manages token estimation, tool-call-safe sliding window message slicing,
    and semantic context summarization for long-running agent threads.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def extract_existing_summary(self, messages: Sequence[BaseMessage]) -> Tuple[str, List[BaseMessage]]:
        """
        Checks if the first message is a previously injected SystemMessage summary.
        Returns (existing_summary_text, remaining_messages).
        """
        if not messages:
            return "", []

        first = messages[0]
        if isinstance(first, SystemMessage) and isinstance(first.content, str) and SUMMARY_PREFIX in first.content:
            content = first.content
            # Extract content inside tags
            match = re.search(r"<conversation_summary>(.*?)</conversation_summary>", content, re.DOTALL)
            summary_text = match.group(1).strip() if match else content.strip()
            return summary_text, list(messages[1:])

        return "", list(messages)

    def _summarize_with_graph_worker(
        self,
        transcript: str,
        previous_summary: str = "",
        max_summary_tokens: int = 1000,
        channel: str = "general"
    ) -> str:
        """Invokes the graph-worker agent via agent_call to produce a stateless, machine-readable summary."""
        from core.agent.prompts import build_summarization_prompt
        prompt = build_summarization_prompt(
            transcript=transcript,
            previous_summary=previous_summary,
            max_summary_tokens=max_summary_tokens
        )

        try:
            from tools.agent_call import agent_call
            import asyncio

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(
                        asyncio.run,
                        agent_call.ainvoke({
                            "agent_id": "graph-worker",
                            "prompt": prompt,
                            "channel": channel,
                            "caller": "context-pruner"
                        })
                    )
                    tool_res = future.result(timeout=10)
            else:
                tool_res = asyncio.run(agent_call.ainvoke({
                    "agent_id": "graph-worker",
                    "prompt": prompt,
                    "channel": channel,
                    "caller": "context-pruner"
                }))

            if tool_res:
                raw_str = str(tool_res)
                m = re.search(r"<summary>(.*?)</summary>", raw_str, re.DOTALL)
                if m:
                    return m.group(1).strip()
                payload_m = re.search(r"<payload>(.*?)</payload>", raw_str, re.DOTALL)
                if payload_m:
                    return payload_m.group(1).strip()
                return raw_str.strip()
        except Exception:
            pass
        return ""

    def summarize_messages(
        self,
        older_messages: Sequence[BaseMessage],
        previous_summary: str = "",
        max_summary_tokens: int = 1000,
        channel: str = "general"
    ) -> str:
        """
        Summarizes older messages by delegating to graph-worker via agent_call,
        falling back to deterministic heuristic extraction.
        """
        if not older_messages and previous_summary:
            return previous_summary

        formatted_lines = [_format_message_for_summary(m) for m in older_messages]
        transcript = "\n".join(line for line in formatted_lines if line)

        # 1. Delegate to stateless graph-worker agent via agent_call
        worker_summary = self._summarize_with_graph_worker(
            transcript=transcript,
            previous_summary=previous_summary,
            max_summary_tokens=max_summary_tokens,
            channel=channel
        )
        if worker_summary:
            return worker_summary

        # 2. Deterministic heuristic fallback (0 token cost)
        return build_heuristic_summary(older_messages, previous_summary=previous_summary)

    def prune_messages(
        self,
        messages: List[BaseMessage],
        max_tokens: Optional[int] = None,
        window_messages: Optional[int] = None,
        max_summary_tokens: Optional[int] = None,
        force: bool = False,
        channel: str = "general"
    ) -> List[BaseMessage]:
        """
        Applies a tool-call-safe sliding window and summarizes older messages if
        token count or message count exceeds the configured threshold.
        """
        if not messages or len(messages) <= 1:
            return messages

        # Check global config
        if not force and not self.config.context_pruning_enabled:
            return messages

        token_threshold = max_tokens or self.config.context_max_tokens
        window_size = window_messages or self.config.context_window_messages
        summary_tokens = max_summary_tokens or self.config.context_summary_max_tokens

        total_tokens = estimate_total_tokens(messages)
        total_count = len(messages)

        # Trigger check: exceeded token threshold OR exceeded message count cap
        if not force and total_tokens <= token_threshold and total_count <= (window_size + 5):
            return messages

        # Extract any existing summary message
        prev_summary, clean_messages = self.extract_existing_summary(messages)
        if len(clean_messages) <= window_size:
            # If after extracting existing summary we are within the window, return with summary
            if prev_summary:
                return [
                    SystemMessage(content=f"{SUMMARY_PREFIX}\n{prev_summary}\n{SUMMARY_SUFFIX}"),
                    *clean_messages
                ]
            return messages

        # Find safe boundary
        split_idx = find_safe_boundary(clean_messages, window_messages=window_size)
        if split_idx <= 0:
            if prev_summary:
                return [
                    SystemMessage(content=f"{SUMMARY_PREFIX}\n{prev_summary}\n{SUMMARY_SUFFIX}"),
                    *clean_messages
                ]
            return clean_messages

        older_messages = clean_messages[:split_idx]
        recent_messages = clean_messages[split_idx:]

        # Summarize older messages via graph-worker or heuristic
        new_summary = self.summarize_messages(
            older_messages,
            previous_summary=prev_summary,
            max_summary_tokens=summary_tokens,
            channel=channel
        )

        summary_msg = SystemMessage(
            content=f"{SUMMARY_PREFIX}\n{new_summary}\n{SUMMARY_SUFFIX}"
        )

        pruned = [summary_msg] + recent_messages
        print(
            f"ContextPruner: Pruned history from {total_count} msgs (~{total_tokens} tokens) "
            f"-> {len(pruned)} msgs (~{estimate_total_tokens(pruned)} tokens)"
        )
        return pruned
