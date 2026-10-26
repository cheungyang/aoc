"""LangGraph's checkpoint saver, on the session tables in memory.db.

A thread's checkpoints and pending writes are rows of its ctx_ table, beside
the session's transcript; all of the SQL is SessionStore's
(core/storage/sessions.py). This module keeps the LangGraph side: the saver
contract, the blobs (pickled, then zlib-compressed) and the history cleanup
done on read.
"""
import pickle
import json
import zlib
import re
from typing import Optional, List, Iterator, Sequence, Any, Dict
from collections import defaultdict
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    ChannelVersions,
    PendingWrite
)

from core.storage.sessions import SessionStore, sanitize_table_name
from core.config.settings import Config


class UnreconstructableError(RuntimeError):
    """Stand-in for an exception that could not survive a pickle round trip.

    Carries the original type and message as text so the diagnostic survives
    even though the original object could not.
    """


def _make_picklable(value: Any) -> Any:
    """Replaces exceptions that cannot be unpickled with a surrogate that can.

    `BaseException.__reduce__` reconstructs via `type(exc)(*exc.args)`. Any
    exception whose `__init__` requires arguments beyond `args` therefore
    pickles cleanly but raises `TypeError` on load. `openai.APIError` is one:
    its signature is `(message, request, *, body)` while `args` holds only the
    message.

    LangGraph stores failures as a `("__error__", exc)` pending write, so such
    an exception is written into the checkpoint and then makes the entire
    session permanently unreadable. Verifying the round trip here keeps a bad
    object out of the database in the first place.
    """
    if not isinstance(value, BaseException):
        return value
    try:
        pickle.loads(pickle.dumps(value))
        return value
    except Exception:
        origin = f"{type(value).__module__}.{type(value).__qualname__}"
        return UnreconstructableError(f"{origin}: {value}")



def _evict_base64_from_content(content: Any) -> Any:
    """Evicts large base64 payloads from historical tool messages."""
    if isinstance(content, str):
        if len(content) < 500:
            return content

        # 1. Match filesystem read_image XML tag
        content = re.sub(
            r'<instruction_result action="read_image" path="([^"]+)">[A-Za-z0-9+/=\s\r\n]{500,}</instruction_result>',
            r'<instruction_result action="read_image" path="\1">[Image base64 data evicted after visual processing - path: \1]</instruction_result>',
            content
        )

        # 2. Match data URIs
        content = re.sub(
            r'data:image/[a-zA-Z+]+;base64,[A-Za-z0-9+/=\s\r\n]{500,}',
            r'[Image data URI evicted after visual processing]',
            content
        )

        # 3. Match pure large base64 strings inside tool payload tags
        def replace_large_payload_b64(m):
            return f"<payload>[Image base64 payload evicted ({len(m.group(1))} chars)]</payload>"

        content = re.sub(
            r'<payload>\s*([A-Za-z0-9+/=\s\r\n]{2000,})\s*</payload>',
            replace_large_payload_b64,
            content
        )
        return content

    elif isinstance(content, list):
        new_list = []
        for part in content:
            if isinstance(part, dict):
                p = dict(part)
                if p.get("type") == "image_url" and isinstance(p.get("image_url"), dict):
                    url = p["image_url"].get("url", "")
                    if url.startswith("data:image/") and len(url) > 500:
                        p["image_url"] = {"url": "[Image data URI evicted after visual processing]"}
                elif "text" in p and isinstance(p["text"], str):
                    p["text"] = _evict_base64_from_content(p["text"])
                new_list.append(p)
            else:
                new_list.append(part)
        return new_list

    return content


def _sanitize_checkpoint_messages(checkpoint: Any) -> Any:
    """
    Ensures every AIMessage with tool_calls in checkpoint history is followed by matching ToolMessages,
    and evicts historical large base64 image data from past tool messages that have already been evaluated.
    """
    if not isinstance(checkpoint, dict):
        return checkpoint
    channel_values = checkpoint.get("channel_values")
    if not isinstance(channel_values, dict) or "messages" not in channel_values:
        return checkpoint

    from langchain_core.messages import AIMessage, ToolMessage
    messages = channel_values.get("messages", [])
    if not isinstance(messages, list) or not messages:
        return checkpoint

    sanitized = []
    pending_tool_calls = {}

    for msg in messages:
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for tc_id, name in list(pending_tool_calls.items()):
                sanitized.append(ToolMessage(
                    content=f"Tool '{name}' execution was interrupted.",
                    tool_call_id=tc_id,
                    name=name
                ))
            pending_tool_calls.clear()

            sanitized.append(msg)
            for tc in msg.tool_calls:
                tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "tool")
                if tc_id:
                    pending_tool_calls[tc_id] = tc_name
        elif isinstance(msg, ToolMessage):
            sanitized.append(msg)
            tc_id = getattr(msg, "tool_call_id", None)
            if tc_id in pending_tool_calls:
                del pending_tool_calls[tc_id]
        else:
            for tc_id, name in list(pending_tool_calls.items()):
                sanitized.append(ToolMessage(
                    content=f"Tool '{name}' execution was interrupted.",
                    tool_call_id=tc_id,
                    name=name
                ))
            pending_tool_calls.clear()
            sanitized.append(msg)

    for tc_id, name in list(pending_tool_calls.items()):
        sanitized.append(ToolMessage(
            content=f"Tool '{name}' execution was interrupted.",
            tool_call_id=tc_id,
            name=name
        ))

    # Historical base64 eviction: find the last AIMessage index
    last_ai_index = max((i for i, m in enumerate(sanitized) if isinstance(m, AIMessage)), default=-1)
    if last_ai_index > 0:
        for idx in range(last_ai_index):
            m = sanitized[idx]
            if isinstance(m, ToolMessage) and getattr(m, "content", None):
                evicted_content = _evict_base64_from_content(m.content)
                if evicted_content != m.content:
                    # Update tool message with evicted content
                    sanitized[idx] = ToolMessage(
                        content=evicted_content,
                        tool_call_id=getattr(m, "tool_call_id", ""),
                        name=getattr(m, "name", None),
                        additional_kwargs=getattr(m, "additional_kwargs", {}),
                        id=getattr(m, "id", None)
                    )

    channel_values["messages"] = sanitized
    return checkpoint



class SqliteCheckpointer(BaseCheckpointSaver):
    def __init__(self, path: Optional[str] = None):
        super().__init__()
        # Resolved once, like the file this saver was built on. Opening it now
        # creates the file eagerly: a saver that can't open it fails here.
        self.store = SessionStore(path or Config().memory_db_path)
        with self.store.connect():
            pass

    def _serialize_blob(self, obj: Any) -> bytes:
        """Serializes and zlib-compresses a Python object to save disk space."""
        raw = pickle.dumps(obj)
        return zlib.compress(raw, level=6)

    def _deserialize_blob(self, blob: bytes) -> Any:
        """Decompresses and deserializes a Python object, supporting backwards-compatible uncompressed blobs."""
        if not blob:
            return None
        try:
            raw = zlib.decompress(blob)
        except zlib.error:
            # Legacy uncompressed blob: the pickle stream was stored directly.
            raw = blob
        # Deliberately not guarded. A pickle failure here is a real problem with
        # the stored object; retrying it against the still-compressed bytes only
        # ever yields "invalid load key, 'x'" (0x78 being the zlib header), which
        # hides the actual cause.
        return pickle.loads(raw)

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id = config["configurable"].get("thread_id")
        if not thread_id:
            return None

        table_name = sanitize_table_name(thread_id)
        row, write_rows = self.store.checkpoint(table_name, config["configurable"].get("checkpoint_id"))
        if not row:
            return None

        checkpoint_data = self._deserialize_blob(row["data"])
        checkpoint = _sanitize_checkpoint_messages(checkpoint_data.get("checkpoint"))
        metadata = checkpoint_data.get("metadata", {})
        entry_config = checkpoint_data.get("config", {})
        parent_config = checkpoint_data.get("parent_config")

        # Pending writes for this checkpoint if any
        cp_id = row["checkpoint_id"]
        pending_writes = []
        for w_row in write_rows:
            try:
                write_info = self._deserialize_blob(w_row["data"])
            except Exception as e:
                # Pending writes are resumption state for an interrupted
                # turn, not conversation history. Dropping one loses at most
                # an in-flight retry, whereas propagating makes the thread
                # unusable forever. Databases written before _make_picklable
                # existed can contain such rows.
                print(
                    f"SqliteCheckpointer: skipping unreadable pending write in "
                    f"{table_name} (checkpoint {cp_id}): {type(e).__name__}: {e}"
                )
                continue
            if isinstance(write_info, dict) and "writes" in write_info:
                for w in write_info["writes"]:
                    pending_writes.append((write_info.get("task_id", ""), w[0], w[1]))
            elif isinstance(write_info, tuple) or isinstance(write_info, list):
                pending_writes.append(write_info)

        def normalize_entry_config(cfg_in):
            cfg = {
                **(cfg_in or {}),
                "configurable": {
                    **(cfg_in.get("configurable", {}) if cfg_in else {})
                }
            }
            if "checkpoint_ns" not in cfg["configurable"]:
                cfg["configurable"]["checkpoint_ns"] = config["configurable"].get("checkpoint_ns", "")
            return cfg

        return CheckpointTuple(
            config=normalize_entry_config(entry_config),
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes if pending_writes else None
        )

    def put(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        thread_id = config["configurable"].get("thread_id")
        if not thread_id:
            raise ValueError("thread_id is required in config")

        table_name = sanitize_table_name(thread_id)
        checkpoint_id = checkpoint["id"]

        configurable = {}
        for k, v in config.get("configurable", {}).items():
            if not k.startswith("__"):
                configurable[k] = v
        configurable["checkpoint_id"] = checkpoint_id

        return_config = {
            "configurable": configurable
        }

        entry_data = {
            "config": return_config,
            "checkpoint": checkpoint,
            "metadata": metadata,
            "new_versions": new_versions
        }

        blob = self._serialize_blob(entry_data)
        step = metadata.get("step", -1) if isinstance(metadata, dict) else -1
        meta_json = json.dumps(metadata) if isinstance(metadata, dict) else str(metadata)
        self.store.add_checkpoint(table_name, checkpoint_id, step, blob, meta_json, json.dumps(return_config))

        return return_config

    def put_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        thread_id = config["configurable"].get("thread_id")
        if not thread_id:
            return

        table_name = sanitize_table_name(thread_id)
        checkpoint_id = config["configurable"].get("checkpoint_id")

        write_data = {
            # LangGraph records task failures as a ("__error__", exc) write. If
            # that exception cannot be unpickled, every later read of this
            # session fails, so it is swapped for a surrogate before storage.
            "writes": [(channel, _make_picklable(value)) for channel, value in writes],
            "task_id": task_id,
            "task_path": task_path,
            "checkpoint_id": checkpoint_id
        }
        blob = self._serialize_blob(write_data)
        self.store.add_write(table_name, checkpoint_id, blob)

    def list(self, config: RunnableConfig | None, *, filter: dict[str, Any] | None = None, before: RunnableConfig | None = None, limit: int | None = None) -> Iterator[CheckpointTuple]:
        all_checkpoints = []
        table_name = None  # every active thread
        if config and config.get("configurable", {}).get("thread_id"):
            table_name = sanitize_table_name(config["configurable"]["thread_id"])

        for row in self.store.checkpoints(table_name):
            cp_id = row["checkpoint_id"]
            if before and before["configurable"].get("checkpoint_id") == cp_id:
                continue

            try:
                checkpoint_data = self._deserialize_blob(row["data"])
            except Exception:
                continue

            metadata = checkpoint_data.get("metadata", {})
            if filter:
                match = True
                for k, v in filter.items():
                    if metadata.get(k) != v:
                        match = False
                        break
                if not match:
                    continue

            all_checkpoints.append(CheckpointTuple(
                config=checkpoint_data.get("config", {}),
                checkpoint=checkpoint_data.get("checkpoint"),
                metadata=metadata,
                parent_config=checkpoint_data.get("parent_config"),
                pending_writes=checkpoint_data.get("pending_writes")
            ))

        all_checkpoints.sort(key=lambda x: (x.metadata.get("step", -1) if isinstance(x.metadata, dict) else -1, x.checkpoint.get("id", "") if isinstance(x.checkpoint, dict) else ""), reverse=True)

        if limit:
            all_checkpoints = all_checkpoints[:limit]

        return iter(all_checkpoints)

    def delete_thread(self, thread_id: str) -> None:
        # Drops the session's whole table, its transcript and token rows included.
        self.store.drop_table(sanitize_table_name(thread_id))

    def rollback_last_step(self, thread_id: str) -> None:
        self.store.delete_last_checkpoint(sanitize_table_name(thread_id))

    def archive_thread(self, thread_id: str) -> str:
        archive_table_name = self.store.archive_table(sanitize_table_name(thread_id))
        if archive_table_name:
            return f"Thread {thread_id} archived to table {archive_table_name}"
        return "No active thread table found to archive."

    def archive_all(self) -> str:
        responses = [f"Archived {table_name} to {archive_table_name}"
                     for table_name, archive_table_name in self.store.archive_all()]
        return "\n".join(responses) if responses else "No active threads found to archive."

    def vacuum(self) -> None:
        """Reclaims unused space in SQLite database."""
        self.store.vacuum()

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        return self.get_tuple(config)

    async def aput(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        return self.put_writes(config, writes, task_id, task_path)

    async def alist(self, config: RunnableConfig | None, *, filter: dict[str, Any] | None = None, before: RunnableConfig | None = None, limit: int | None = None):
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item
