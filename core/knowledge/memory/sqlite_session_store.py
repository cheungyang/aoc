import os
import sqlite3
import json
import time
from typing import List, Dict, Any, Optional

SESSIONS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "sessions"))
DEFAULT_DB_PATH = os.path.join(SESSIONS_DIR, "memory.db")


def sanitize_table_name(session_id: str) -> str:
    """Sanitizes a session/context ID into a valid SQLite table name."""
    clean = "".join(c if c.isalnum() or c == "_" else "_" for c in session_id)
    return f"ctx_{clean}"


class SqliteSessionStore:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

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
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cached_tokens REAL DEFAULT 0.0,
            created_at REAL NOT NULL
        );
        """)
        conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table_name}_type" ON "{table_name}" (entry_type);')
        conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table_name}_cp_id" ON "{table_name}" (checkpoint_id);')
        conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table_name}_created" ON "{table_name}" (created_at);')

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        cursor = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        return cursor.fetchone() is not None

    def append_message(self, session_id: str, from_user: str, message: Any) -> str:
        table_name = sanitize_table_name(session_id)
        now = time.time()
        if isinstance(message, (list, dict)):
            try:
                message = json.dumps(message)
            except Exception:
                message = str(message)
        elif not isinstance(message, str):
            message = str(message) if message is not None else ""
        from_user = str(from_user) if from_user is not None else ""
        with self._get_connection() as conn:
            self._ensure_table(conn, table_name)
            conn.execute(
                f"""
                INSERT INTO "{table_name}" (
                    entry_type, from_role, message, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                ("message", from_user, message, now)
            )
            conn.commit()
        return f"Appended message to {session_id}"

    def append_token_usage(self, session_id: str, model: str, input_token: int, output_token: int, cached_token: float) -> str:
        table_name = sanitize_table_name(session_id)
        now = time.time()
        with self._get_connection() as conn:
            self._ensure_table(conn, table_name)
            conn.execute(
                f"""
                INSERT INTO "{table_name}" (
                    entry_type, model, input_tokens, output_tokens, cached_tokens, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("token", model, input_token, output_token, cached_token, now)
            )
            conn.commit()
        return f"Appended token usage to {session_id}"

    def archive_session(self, session_id: str) -> str:
        table_name = sanitize_table_name(session_id)
        ts = int(time.time())
        archive_table_name = f"{table_name}_archived_{ts}"

        with self._get_connection() as conn:
            if self._table_exists(conn, table_name):
                conn.execute(f'ALTER TABLE "{table_name}" RENAME TO "{archive_table_name}"')
                conn.commit()
                return f"Session {session_id} archived to table {archive_table_name}"
            return "No active session table found to archive."

    def archive_all_sessions(self) -> str:
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
        return "\n".join(responses) if responses else "No active sessions found to archive."

    def load_history(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        table_name = sanitize_table_name(session_id)
        with self._get_connection() as conn:
            if not self._table_exists(conn, table_name):
                return []

            cursor = conn.execute(
                f'SELECT from_role, message, created_at FROM "{table_name}" WHERE entry_type = \'message\' ORDER BY id ASC'
            )
            rows = cursor.fetchall()
            data = [{"from": r["from_role"], "message": r["message"], "ts": int(r["created_at"])} for r in rows]
            if limit and limit > 0:
                return data[-limit:]
            return data

    def load_token_history(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        table_name = sanitize_table_name(session_id)
        with self._get_connection() as conn:
            if not self._table_exists(conn, table_name):
                return []

            cursor = conn.execute(
                f'SELECT model, input_tokens, output_tokens, cached_tokens, created_at FROM "{table_name}" WHERE entry_type = \'token\' ORDER BY id ASC'
            )
            rows = cursor.fetchall()
            data = [
                {
                    "ts": int(r["created_at"]),
                    "model": r["model"],
                    "input_token": r["input_tokens"],
                    "output_token": r["output_tokens"],
                    "cached_token": r["cached_tokens"]
                }
                for r in rows
            ]
            if limit and limit > 0:
                return data[-limit:]
            return data

    def list_active_sessions(self) -> List[str]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ctx_%' AND name NOT LIKE '%_archived_%'"
            )
            return [row["name"] for row in cursor.fetchall()]
