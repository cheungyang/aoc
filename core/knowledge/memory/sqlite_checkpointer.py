import os
import sqlite3
import pickle
import json
import time
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

SESSIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "sessions"))
DEFAULT_DB_PATH = os.path.join(SESSIONS_DIR, "memory.db")


def sanitize_table_name(thread_id: str) -> str:
    """Sanitizes a thread/context ID into a valid SQLite table name."""
    clean = "".join(c if c.isalnum() or c == "_" else "_" for c in thread_id)
    return f"ctx_{clean}"


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
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        super().__init__()
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _init_db(self):
        # Trigger directory and initial connection check
        with self._get_connection() as conn:
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
            decompressed = zlib.decompress(blob)
            return pickle.loads(decompressed)
        except Exception:
            # Fallback for uncompressed legacy blobs
            return pickle.loads(blob)

    def _ensure_table(self, conn: sqlite3.Connection, table_name: str):
        conn.execute(f"""
        CREATE TABLE IF NOT EXISTS "{table_name}" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_type TEXT NOT NULL,
            checkpoint_id TEXT,
            step INTEGER DEFAULT -1,
            data BLOB,
            metadata TEXT,
            config TEXT,
            parent_config TEXT,
            from_role TEXT,
            message TEXT,
            model TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cached_tokens REAL,
            execution_time REAL DEFAULT 0.0,
            created_at REAL NOT NULL
        )
        """)
        # Auto-migrate existing tables that don't have execution_time column
        columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table_name}");').fetchall()]
        if "execution_time" not in columns:
            conn.execute(f'ALTER TABLE "{table_name}" ADD COLUMN execution_time REAL DEFAULT 0.0;')
        # Index for efficient lookups by entry_type, step, and checkpoint_id
        conn.execute(f"""
        CREATE INDEX IF NOT EXISTS "idx_{table_name}_entry_step"
        ON "{table_name}" (entry_type, step, id)
        """)
        conn.execute(f"""
        CREATE INDEX IF NOT EXISTS "idx_{table_name}_cpid"
        ON "{table_name}" (entry_type, checkpoint_id)
        """)

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        cursor = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,)
        )
        return cursor.fetchone() is not None

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id = config["configurable"].get("thread_id")
        if not thread_id:
            return None

        table_name = sanitize_table_name(thread_id)
        with self._get_connection() as conn:
            if not self._table_exists(conn, table_name):
                return None

            checkpoint_id = config["configurable"].get("checkpoint_id")
            
            if checkpoint_id:
                cursor = conn.execute(
                    f'SELECT * FROM "{table_name}" WHERE entry_type = \'checkpoint\' AND checkpoint_id = ? LIMIT 1',
                    (checkpoint_id,)
                )
            else:
                cursor = conn.execute(
                    f'SELECT * FROM "{table_name}" WHERE entry_type = \'checkpoint\' ORDER BY step DESC, id DESC LIMIT 1'
                )

            row = cursor.fetchone()
            if not row:
                return None

            checkpoint_data = self._deserialize_blob(row["data"])
            checkpoint = _sanitize_checkpoint_messages(checkpoint_data.get("checkpoint"))
            metadata = checkpoint_data.get("metadata", {})
            entry_config = checkpoint_data.get("config", {})
            parent_config = checkpoint_data.get("parent_config")

            # Fetch pending writes for this checkpoint if any
            cp_id = row["checkpoint_id"]
            writes_cursor = conn.execute(
                f'SELECT data FROM "{table_name}" WHERE entry_type = \'write\' AND checkpoint_id = ?',
                (cp_id,)
            )
            pending_writes = []
            for w_row in writes_cursor.fetchall():
                write_info = self._deserialize_blob(w_row["data"])
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
        now = time.time()

        with self._get_connection() as conn:
            self._ensure_table(conn, table_name)
            conn.execute(
                f"""
                INSERT INTO "{table_name}" (
                    entry_type, checkpoint_id, step, data, metadata, config, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("checkpoint", checkpoint_id, step, blob, meta_json, json.dumps(return_config), now)
            )

            # Prune older intermediate checkpoints (keep last 10 snapshots) to prevent exponential bloat
            conn.execute(
                f"""
                DELETE FROM "{table_name}"
                WHERE entry_type = 'checkpoint'
                AND id NOT IN (
                    SELECT id FROM "{table_name}"
                    WHERE entry_type = 'checkpoint'
                    ORDER BY step DESC, id DESC
                    LIMIT 10
                )
                """
            )
            # Prune orphaned writes
            conn.execute(
                f"""
                DELETE FROM "{table_name}"
                WHERE entry_type = 'write'
                AND checkpoint_id NOT IN (
                    SELECT checkpoint_id FROM "{table_name}"
                    WHERE entry_type = 'checkpoint'
                )
                """
            )
            conn.commit()

        return return_config

    def put_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        thread_id = config["configurable"].get("thread_id")
        if not thread_id:
            return

        table_name = sanitize_table_name(thread_id)
        checkpoint_id = config["configurable"].get("checkpoint_id")
        now = time.time()

        write_data = {
            "writes": writes,
            "task_id": task_id,
            "task_path": task_path,
            "checkpoint_id": checkpoint_id
        }
        blob = self._serialize_blob(write_data)

        with self._get_connection() as conn:
            self._ensure_table(conn, table_name)
            conn.execute(
                f"""
                INSERT INTO "{table_name}" (
                    entry_type, checkpoint_id, data, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                ("write", checkpoint_id, blob, now)
            )
            conn.commit()

    def list(self, config: RunnableConfig | None, *, filter: dict[str, Any] | None = None, before: RunnableConfig | None = None, limit: int | None = None) -> Iterator[CheckpointTuple]:
        all_checkpoints = []
        with self._get_connection() as conn:
            tables_to_query = []
            if config and config.get("configurable", {}).get("thread_id"):
                tname = sanitize_table_name(config["configurable"]["thread_id"])
                if self._table_exists(conn, tname):
                    tables_to_query.append(tname)
            else:
                cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ctx_%' AND name NOT LIKE '%_archived_%'")
                tables_to_query = [r["name"] for r in cursor.fetchall()]

            for table_name in tables_to_query:
                cursor = conn.execute(f'SELECT * FROM "{table_name}" WHERE entry_type = \'checkpoint\' ORDER BY step DESC, id DESC')
                for row in cursor.fetchall():
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
        table_name = sanitize_table_name(thread_id)
        with self._get_connection() as conn:
            if self._table_exists(conn, table_name):
                conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
                conn.commit()

    def rollback_last_step(self, thread_id: str) -> None:
        table_name = sanitize_table_name(thread_id)
        with self._get_connection() as conn:
            if self._table_exists(conn, table_name):
                cursor = conn.execute(f'SELECT checkpoint_id FROM "{table_name}" WHERE entry_type = \'checkpoint\' ORDER BY step DESC, id DESC LIMIT 1')
                row = cursor.fetchone()
                if row:
                    cp_id = row["checkpoint_id"]
                    conn.execute(f'DELETE FROM "{table_name}" WHERE checkpoint_id = ?', (cp_id,))
                    conn.commit()

    def archive_thread(self, thread_id: str) -> str:
        table_name = sanitize_table_name(thread_id)
        ts = int(time.time())
        archive_table_name = f"{table_name}_archived_{ts}"
        with self._get_connection() as conn:
            if self._table_exists(conn, table_name):
                conn.execute(f'ALTER TABLE "{table_name}" RENAME TO "{archive_table_name}"')
                conn.commit()
                return f"Thread {thread_id} archived to table {archive_table_name}"
            return "No active thread table found to archive."

    def archive_all(self) -> str:
        ts = int(time.time())
        responses = []
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ctx_%' AND name NOT LIKE '%_archived_%'"
            )
            tables = [row["name"] for row in cursor.fetchall()]
            for table_name in tables:
                archive_table_name = f"{table_name}_archived_{ts}"
                conn.execute(f'ALTER TABLE "{table_name}" RENAME TO "{archive_table_name}"')
                responses.append(f"Archived {table_name} to {archive_table_name}")
            conn.commit()
        return "\n".join(responses) if responses else "No active threads found to archive."

    def vacuum(self) -> None:
        """Reclaims unused space in SQLite database."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        return self.get_tuple(config)

    async def aput(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        return self.put_writes(config, writes, task_id, task_path)

    async def alist(self, config: RunnableConfig | None, *, filter: dict[str, Any] | None = None, before: RunnableConfig | None = None, limit: int | None = None):
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item
