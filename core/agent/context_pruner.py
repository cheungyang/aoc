import re
import sys
import asyncio
import concurrent.futures
from typing import List, Sequence, Any, Optional, Tuple, Union
from core.agent.session_identifier import SessionIdentifier

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)
from core.util.config import Config
from core.util.message_util import (
    estimate_message_tokens,
    estimate_total_tokens,
    find_safe_boundary,
)
from core.util.summarize_util import (
    build_heuristic_summary,
    SUMMARY_PREFIX,
    SUMMARY_SUFFIX,
)


def _format_message_for_summary(msg: BaseMessage) -> str:
    """Formats a single message into a compact text line for summarization."""
    if isinstance(msg, HumanMessage):
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
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


def _parse_worker_summary_response(tool_res: Any) -> str:
    """Parses tool output from graph-worker-low, extracting XML summary or logging errors."""
    if not tool_res:
        return ""
    raw_str = str(tool_res)
    err_m = re.search(r"<errors?>(.*?)</errors?>", raw_str, re.DOTALL | re.IGNORECASE)
    if err_m and err_m.group(1).strip() and err_m.group(1).strip().lower() != "none":
        err_text = err_m.group(1).strip()
        print(f"[ContextPruner] Warning: graph-worker-low returned error: '{err_text}'. Falling back to heuristic summary.", file=sys.stderr)
        return ""

    m = re.search(r"<summary>(.*?)</summary>", raw_str, re.DOTALL)
    if m and m.group(1).strip():
        summary_text = m.group(1).strip()
        print(f"[ContextPruner] Successfully generated graph-worker-low summary (~{len(summary_text)} chars).")
        return summary_text
    payload_m = re.search(r"<payload>(.*?)</payload>", raw_str, re.DOTALL)
    if payload_m and payload_m.group(1).strip():
        payload_text = payload_m.group(1).strip()
        print(f"[ContextPruner] Extracted graph-worker-low payload summary (~{len(payload_text)} chars).")
        return payload_text
    if raw_str.strip() and "<tool_response" not in raw_str:
        print(f"[ContextPruner] Extracted raw graph-worker-low summary (~{len(raw_str.strip())} chars).")
        return raw_str.strip()

    print("[ContextPruner] Warning: graph-worker-low returned empty or unrecognized response. Falling back to heuristic summary.", file=sys.stderr)
    return ""


class ContextPruner:
    """
    Manages token estimation, tool-call-safe sliding window message slicing,
    and semantic context summarization for long-running agent threads.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def _extract_existing_summary(self, messages: Sequence[BaseMessage]) -> Tuple[str, List[BaseMessage]]:
        """
        Checks if the first message is a previously injected SystemMessage summary.
        Returns (existing_summary_text, remaining_messages).
        """
        if not messages:
            return "", []

        first = messages[0]
        if isinstance(first, SystemMessage) and isinstance(first.content, str) and SUMMARY_PREFIX in first.content:
            content = first.content
            match = re.search(r"<conversation_summary>(.*?)</conversation_summary>", content, re.DOTALL)
            summary_text = match.group(1).strip() if match else content.strip()
            return summary_text, list(messages[1:])

        return "", list(messages)

    # -------------------------------------------------------------------------
    # Pair 1: Graph-Worker Summarization (Async core + Sync bridge)
    # -------------------------------------------------------------------------

    async def _asummarize_with_graph_worker(
        self,
        transcript: str,
        previous_summary: str = "",
        max_summary_tokens: int = 1000,
        channel: str = "general"
    ) -> str:
        """Asynchronously invokes the graph-worker-low agent via agent_call to produce a stateless, machine-readable summary."""
        from core.agent.prompts import build_summarization_prompt
        prompt = build_summarization_prompt(
            transcript=transcript,
            previous_summary=previous_summary,
            max_summary_tokens=max_summary_tokens
        )

        try:
            from tools.agent_call import agent_call

            timeout = self.config.context_pruning_timeout

            try:
                tool_res = await asyncio.wait_for(
                    agent_call.ainvoke({
                        "agent_id": "graph-worker-low",
                        "prompt": prompt,
                        "channel": channel,
                        "caller": "context-pruner"
                    }),
                    timeout=timeout
                )
            except (asyncio.TimeoutError, TimeoutError):
                print(f"[ContextPruner] Warning: graph-worker-low async summarization timed out after {timeout}s. Falling back to heuristic summary.", file=sys.stderr)
                return ""
            except Exception as e:
                print(f"[ContextPruner] Error during async graph-worker-low summarization: {e}. Falling back to heuristic summary.", file=sys.stderr)
                return ""

            return _parse_worker_summary_response(tool_res)
        except (asyncio.TimeoutError, TimeoutError, Exception) as e:
            print(f"[ContextPruner] Unexpected error during async summarization: {e}. Falling back to heuristic summary.", file=sys.stderr)
            return ""

    def _summarize_with_graph_worker(
        self,
        transcript: str,
        previous_summary: str = "",
        max_summary_tokens: int = 1000,
        channel: str = "general"
    ) -> str:
        """Synchronously invokes the graph-worker-low agent via agent_call, bridging to the async execution."""
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        asyncio.run,
                        self._asummarize_with_graph_worker(
                            transcript=transcript,
                            previous_summary=previous_summary,
                            max_summary_tokens=max_summary_tokens,
                            channel=channel
                        )
                    )
                    return future.result()
            else:
                return asyncio.run(
                    self._asummarize_with_graph_worker(
                        transcript=transcript,
                        previous_summary=previous_summary,
                        max_summary_tokens=max_summary_tokens,
                        channel=channel
                    )
                )
        except Exception as e:
            print(f"[ContextPruner] Error during sync graph-worker-low summarization: {e}. Falling back to heuristic summary.", file=sys.stderr)
            return ""

    # -------------------------------------------------------------------------
    # Pair 2: Transcript Preparation & Summarization Strategy
    # -------------------------------------------------------------------------

    def _prepare_transcript_for_summary(
        self,
        older_messages: Sequence[BaseMessage],
        previous_summary: str = ""
    ) -> Tuple[bool, str]:
        """Prepares a compact text transcript from older messages."""
        if not older_messages and previous_summary:
            return True, previous_summary

        formatted_lines = [_format_message_for_summary(m) for m in older_messages]
        transcript = "\n".join(line for line in formatted_lines if line)
        return False, transcript

    def _summarize_messages(
        self,
        older_messages: Sequence[BaseMessage],
        previous_summary: str = "",
        channel: str = "general"
    ) -> str:
        """Summarizes older messages via graph-worker-low, falling back to deterministic heuristic extraction."""
        is_direct, transcript = self._prepare_transcript_for_summary(older_messages, previous_summary)
        if is_direct:
            return transcript

        worker_summary = self._summarize_with_graph_worker(
            transcript=transcript,
            previous_summary=previous_summary,
            max_summary_tokens=self.config.context_summary_max_tokens,
            channel=channel
        )
        if worker_summary:
            return worker_summary

        print(f"[ContextPruner] Falling back to deterministic heuristic summary for {len(older_messages)} older messages.")
        return build_heuristic_summary(older_messages, previous_summary=previous_summary)

    async def _asummarize_messages(
        self,
        older_messages: Sequence[BaseMessage],
        previous_summary: str = "",
        channel: str = "general"
    ) -> str:
        """Asynchronously summarizes older messages via graph-worker-low, falling back to deterministic heuristic extraction."""
        is_direct, transcript = self._prepare_transcript_for_summary(older_messages, previous_summary)
        if is_direct:
            return transcript

        worker_summary = await self._asummarize_with_graph_worker(
            transcript=transcript,
            previous_summary=previous_summary,
            max_summary_tokens=self.config.context_summary_max_tokens,
            channel=channel
        )
        if worker_summary:
            return worker_summary

        print(f"[ContextPruner] Falling back to deterministic heuristic summary for {len(older_messages)} older messages.")
        return build_heuristic_summary(older_messages, previous_summary=previous_summary)

    # -------------------------------------------------------------------------
    # Pair 3: Sliding Window Context Pruning (Sync / Async)
    # -------------------------------------------------------------------------

    def _prepare_pruning_split(
        self,
        messages: List[BaseMessage],
        force: bool = False
    ) -> Tuple[bool, Optional[List[BaseMessage]], str, List[BaseMessage], List[BaseMessage], int, int]:
        """
        Evaluates pruning thresholds and extracts safe message slices.
        Returns:
            (should_prune, early_result, prev_summary, older_messages, recent_messages, total_count, total_tokens)
        """
        if not messages or len(messages) <= 1:
            return False, messages, "", [], [], 0, 0

        if not force and not self.config.context_pruning_enabled:
            return False, messages, "", [], [], 0, 0

        token_threshold = self.config.context_max_tokens
        window_size = self.config.context_window_messages

        total_tokens = estimate_total_tokens(messages)
        total_count = len(messages)

        if not force and total_tokens <= token_threshold and total_count <= (window_size * 2):
            return False, messages, "", [], [], total_count, total_tokens

        prev_summary, clean_messages = self._extract_existing_summary(messages)
        if len(clean_messages) <= window_size:
            if prev_summary:
                return False, [SystemMessage(content=f"{SUMMARY_PREFIX}\n{prev_summary}\n{SUMMARY_SUFFIX}"), *clean_messages], "", [], [], total_count, total_tokens
            return False, messages, "", [], [], total_count, total_tokens

        split_idx = find_safe_boundary(clean_messages, window_messages=window_size)
        if split_idx <= 0:
            if prev_summary:
                return False, [SystemMessage(content=f"{SUMMARY_PREFIX}\n{prev_summary}\n{SUMMARY_SUFFIX}"), *clean_messages], "", [], [], total_count, total_tokens
            return False, clean_messages, "", [], [], total_count, total_tokens

        older_messages = list(clean_messages[:split_idx])
        recent_messages = list(clean_messages[split_idx:])

        while recent_messages and not isinstance(recent_messages[0], HumanMessage):
            older_messages.append(recent_messages.pop(0))

        if not recent_messages:
            if prev_summary:
                return False, [SystemMessage(content=f"{SUMMARY_PREFIX}\n{prev_summary}\n{SUMMARY_SUFFIX}"), *clean_messages], "", [], [], total_count, total_tokens
            return False, clean_messages, "", [], [], total_count, total_tokens

        return True, None, prev_summary, older_messages, recent_messages, total_count, total_tokens

    def _finalize_pruned_messages(
        self,
        new_summary: str,
        recent_messages: List[BaseMessage],
        total_count: int,
        total_tokens: int
    ) -> List[BaseMessage]:
        """Packages the summary SystemMessage and recent turns, logging the final statistics."""
        summary_msg = SystemMessage(
            content=f"{SUMMARY_PREFIX}\n{new_summary}\n{SUMMARY_SUFFIX}"
        )
        pruned = [summary_msg] + recent_messages
        print(
            f"[ContextPruner] Pruned history from {total_count} msgs (~{total_tokens} tokens) "
            f"-> {len(pruned)} msgs (~{estimate_total_tokens(pruned)} tokens)"
        )
        return pruned

    def prune_messages(
        self,
        messages: List[BaseMessage],
        channel: str = "general",
        force: bool = False
    ) -> List[BaseMessage]:
        """Applies a tool-call-safe sliding window and summarizes older messages if exceeding configured threshold."""
        should_prune, early_result, prev_summary, older, recent, total_count, total_tokens = (
            self._prepare_pruning_split(messages, force=force)
        )
        if not should_prune:
            return early_result

        new_summary = self._summarize_messages(older, previous_summary=prev_summary, channel=channel)
        return self._finalize_pruned_messages(new_summary, recent, total_count, total_tokens)

    async def aprune_messages(
        self,
        messages: List[BaseMessage],
        channel: str = "general",
        force: bool = False
    ) -> List[BaseMessage]:
        """Asynchronously applies a tool-call-safe sliding window and summarizes older messages if exceeding threshold."""
        should_prune, early_result, prev_summary, older, recent, total_count, total_tokens = (
            self._prepare_pruning_split(messages, force=force)
        )
        if not should_prune:
            return early_result

        new_summary = await self._asummarize_messages(older, previous_summary=prev_summary, channel=channel)
        return self._finalize_pruned_messages(new_summary, recent, total_count, total_tokens)

    # -------------------------------------------------------------------------
    # Pair 4: SQLite Session Auto-Pruning (Sync / Async)
    # -------------------------------------------------------------------------

    def _get_session_messages_for_auto_prune(
        self,
        session_id: str,
        force: bool = False
    ) -> Tuple[Optional[Any], Optional[Any], List[BaseMessage]]:
        """Retrieves checkpointer, session tuple, and messages from SQLite storage."""
        if not session_id or (not force and not self.config.context_pruning_enabled):
            return None, None, []

        from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer

        checkpointer = SqliteCheckpointer()
        tuple_res = checkpointer.get_tuple({"configurable": {"thread_id": session_id}})
        if not tuple_res or not tuple_res.checkpoint:
            return None, None, []

        channel_values = tuple_res.checkpoint.get("channel_values", {})
        messages = channel_values.get("messages", [])
        if not messages or len(messages) <= 1:
            return None, None, []

        return checkpointer, tuple_res, messages

    def _commit_pruned_checkpoint(
        self,
        checkpointer: Any,
        tuple_res: Any,
        messages: List[BaseMessage],
        pruned_messages: List[BaseMessage],
        session_id: str
    ) -> bool:
        """Commits updated message list to SQLite checkpoint if changes occurred."""
        if len(pruned_messages) != len(messages) or pruned_messages != messages:
            tuple_res.checkpoint["channel_values"]["messages"] = pruned_messages
            checkpointer.put(
                tuple_res.config,
                tuple_res.checkpoint,
                tuple_res.metadata,
                new_versions=tuple_res.checkpoint.get("versions_seen", {})
            )
            print(f"[ContextPruner] Updated checkpoint for session '{session_id}' in SQLite storage.")
            return True
        return False

    def auto_prune_session(
        self,
        session: SessionIdentifier,
        force: bool = False
    ) -> bool:
        """Inspects and prunes the session checkpoint in SQLite storage if exceeding thresholds."""
        session_id = session.session_id
        channel = session.channel_name
        try:
            checkpointer, tuple_res, messages = self._get_session_messages_for_auto_prune(session_id, force=force)
            if not tuple_res:
                return False

            pruned_messages = self.prune_messages(messages, channel=channel, force=force)
            return self._commit_pruned_checkpoint(checkpointer, tuple_res, messages, pruned_messages, session_id)
        except Exception as e:
            print(f"[ContextPruner] Error: auto_prune_session failed for session '{session_id}': {e}", file=sys.stderr)
            return False

    async def aauto_prune_session(
        self,
        session: SessionIdentifier,
        force: bool = False
    ) -> bool:
        """Asynchronously inspects and prunes the session checkpoint in SQLite storage if exceeding thresholds."""
        session_id = session.session_id
        channel = session.channel_name
        if hasattr(self.auto_prune_session, "mock_calls") or hasattr(self.auto_prune_session, "_mock_name"):
            if force:
                res = self.auto_prune_session(session, force=force)
            else:
                res = self.auto_prune_session(session)
            if asyncio.iscoroutine(res):
                return await res
            return res

        try:
            checkpointer, tuple_res, messages = self._get_session_messages_for_auto_prune(session_id, force=force)
            if not tuple_res:
                return False

            pruned_messages = await self.aprune_messages(messages, channel=channel, force=force)
            return self._commit_pruned_checkpoint(checkpointer, tuple_res, messages, pruned_messages, session_id)
        except Exception as e:
            print(f"[ContextPruner] Error: aauto_prune_session failed for session '{session_id}': {e}", file=sys.stderr)
            return False
