import os
import sqlite3
import pickle
import json
import time
import zlib
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
            created_at REAL NOT NULL
        )
        """)
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
            checkpoint = checkpoint_data.get("checkpoint")
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
