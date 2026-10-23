"""memory.db's session tables: one `ctx_<session>` table per session or LangGraph thread.

Two writers share a session's table (schemas/sessions.sql):

- SqliteCheckpointer (core/knowledge/memory/sqlite_checkpointer.py) keeps
  LangGraph's state in `checkpoint` and `write` rows. It pickles them; the
  checkpoint methods here only store and find the blobs.
- The session store proper adds the readable transcript (`message` rows),
  token use (`token`) and voice turn traces (`turn_trace`).

A table is made by its first write. One made by older code keeps its column
order and gets the columns it lacks, so rows are read by column name. `!new`
archives a session by renaming its tables to `<table>_archived_<epoch>`; they
are kept for the token reports, and no session reads them again.
"""
import json
import re
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.storage.base import Row, Store, load_schema
from core.storage.util import ident, placeholders, table_exists, table_names, where_clause
from core.util.config import Config

PREFIX = "ctx_"
# What archiving puts in a table's name: `<table>_archived_<epoch>`.
ARCHIVED = "_archived_"
# The rows LangGraph replays as a session's history. Message and token rows are the rest.
CHECKPOINT_ENTRIES = ("checkpoint", "write")
# Checkpoints a table keeps: saving one prunes the older ones and their writes.
KEEP_CHECKPOINTS = 10
# Columns added after the first tables were made, and their definitions.
# Rows written before the column existed stay NULL: their surface is unknown, not "text".
_ADDED_COLUMNS = (("execution_time", "REAL DEFAULT 0.0"), ("surface", "TEXT"))
_INDEX = re.compile(r'CREATE INDEX IF NOT EXISTS "([^"]+)"')


def sanitize_table_name(session_id: str) -> str:
    """Sanitizes a session/context ID into a valid SQLite table name.

    Lossy: `a:b`, `a-b` and `a_b` share a table.
    """
    clean = "".join(c if c.isalnum() or c == "_" else "_" for c in session_id)
    return f"ctx_{clean}"


class SessionStore(Store):
    """The session tables in memory.db, at `Config().memory_db_path` unless a path is given.

    The methods that take a session id find its table with sanitize_table_name;
    the others take table names. Each opens its own connection unless `conn=`
    is given. A table is made by its first write (`ensure_table`), never on
    connect: reading a table that isn't there gives nothing back, and a
    hand-built table is left as it is.
    """

    schema = ("sessions",)
    pragmas = ("journal_mode = WAL", "synchronous = NORMAL")
    init_on_connect = False

    def default_path(self) -> str:
        return Config().memory_db_path

    # -- the transcript, token use and turn traces ----------------------------

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
        self._append(table_name, {"entry_type": "message", "from_role": from_user, "message": message,
                                  "created_at": now})
        return f"Appended message to {session_id}"

    def append_token_usage(self, session_id: str, model: str, input_token: int, output_token: int, cached_token: float, execution_time: float = 0.0, surface: Optional[str] = None) -> str:
        table_name = sanitize_table_name(session_id)
        now = time.time()
        self._append(table_name, {"entry_type": "token", "model": model, "input_tokens": input_token,
                                  "output_tokens": output_token, "cached_tokens": cached_token,
                                  "execution_time": execution_time, "surface": surface, "created_at": now})
        return f"Appended token usage to {session_id}"

    def append_turn_trace(self, session_id: str, trace: Dict[str, Any], surface: Optional[str] = "voice") -> str:
        """Stores one turn's latency trace (see core.runtime.turn_trace) beside its token rows.

        The whole trace is JSON in `message`; time to first audio is also put in
        `execution_time` (seconds) so p50/p90 need no JSON parsing. A turn that
        never played audio has a NULL there, not 0, so it can't flatter the median.
        """
        table_name = sanitize_table_name(session_id)
        now = time.time()
        ttfa_ms = trace.get("ttfa_ms")
        self._append(table_name, {"entry_type": "turn_trace", "message": json.dumps(trace, default=str),
                                  "execution_time": None if ttfa_ms is None else ttfa_ms / 1000.0,
                                  "surface": surface, "created_at": now})
        return f"Appended turn trace to {session_id}"

    def archive_session(self, session_id: str) -> str:
        if not session_id:
            return "No active session table found to archive."

        table_name = sanitize_table_name(session_id)
        ts = int(time.time())

        # Collect candidate tokens from session_id to identify graph tables in this session/channel
        clean_session = table_name.removeprefix("ctx_") if table_name.startswith("ctx_") else table_name
        postfix_candidates = set()

        def _clean_token(s: str) -> str:
            return "".join(c if c.isalnum() or c == "_" else "_" for c in s).strip("_")

        if ":" in session_id:
            parts = session_id.split(":")
            if len(parts) >= 3:
                postfix = ":".join(parts[2:])
                clean_pf = _clean_token(postfix)
                if clean_pf:
                    postfix_candidates.add(clean_pf)
                if len(parts) >= 4:
                    # Specific thread session: add thread ID but avoid adding parent channel to prevent over-matching
                    th_token = _clean_token(parts[-1])
                    if th_token:
                        postfix_candidates.add(th_token)
                else:
                    for p in parts[2:]:
                        cp = _clean_token(p)
                        if cp:
                            postfix_candidates.add(cp)
            elif len(parts) == 2:
                clean_src = _clean_token(parts[1])
                if clean_src:
                    postfix_candidates.add(clean_src)
        else:
            cleaned = _clean_token(session_id)
            if cleaned:
                postfix_candidates.add(cleaned)

        responses = []
        with self.connect() as conn:
            active_tables = self.tables(conn=conn)

            matched_tables = []
            for t in active_tables:
                if t == table_name:
                    matched_tables.append(t)
                elif t.endswith(f"_{clean_session}"):
                    matched_tables.append(t)
                elif t.startswith("ctx_graph_"):
                    t_suffix = t.removeprefix("ctx_graph_")
                    if t_suffix == clean_session or t.endswith(f"_{clean_session}"):
                        matched_tables.append(t)
                    elif any(cand and (t.endswith(f"_{cand}") or t_suffix == cand) for cand in postfix_candidates):
                        matched_tables.append(t)

            for t in matched_tables:
                archive_table_name = self._archive(conn, t, ts)
                if t == table_name:
                    responses.append(f"Session {session_id} archived to table {archive_table_name}")
                else:
                    responses.append(f"Archived {t} to {archive_table_name}")

        return "\n".join(responses) if responses else "No active session table found to archive."

    def archive_all_sessions(self) -> str:
        responses = [f"Archived {table} to {archived}" for table, archived in self.archive_all()]
        return "\n".join(responses) if responses else "No active sessions found to archive."

    def load_history(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        table_name = sanitize_table_name(session_id)
        with self.connect() as conn:
            if not table_exists(conn, table_name):
                return []

            rows = self.query(
                f"SELECT from_role, message, created_at FROM {ident(table_name)} WHERE entry_type = 'message' ORDER BY id ASC",
                conn=conn,
            )
            data = [{"from": r["from_role"], "message": r["message"], "ts": int(r["created_at"])} for r in rows]
            if limit and limit > 0:
                return data[-limit:]
            return data

    def load_token_history(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        table_name = sanitize_table_name(session_id)
        with self.connect() as conn:
            if not table_exists(conn, table_name):
                return []

            self.ensure_table(table_name, conn=conn)
            rows = self.query(
                f"SELECT model, input_tokens, output_tokens, cached_tokens, execution_time, surface, created_at "
                f"FROM {ident(table_name)} WHERE entry_type = 'token' ORDER BY id ASC",
                conn=conn,
            )
            data = [
                {
                    "ts": int(r["created_at"]),
                    "model": r["model"],
                    "input_token": r["input_tokens"],
                    "output_token": r["output_tokens"],
                    "cached_token": r["cached_tokens"],
                    "execution_time": r["execution_time"] if "execution_time" in r.keys() and r["execution_time"] is not None else 0.0,
                    "surface": r["surface"],
                }
                for r in rows
            ]
            if limit and limit > 0:
                return data[-limit:]
            return data

    def list_active_sessions(self) -> List[str]:
        """The active tables' names (not session ids), checkpoint-only ones included."""
        return self.tables()

    # -- LangGraph's checkpoints (core/knowledge/memory/sqlite_checkpointer.py) -

    def add_checkpoint(self, table: str, checkpoint_id: str, step: int, data: bytes, metadata: str, config: str,
                       *, keep: int = KEEP_CHECKPOINTS) -> None:
        """Saves a checkpoint row, then prunes `table` to its newest `keep`
        checkpoints and their writes, all in one transaction.

        An id already saved is saved again (ContextPruner re-saves a pruned
        checkpoint under its own id): see `checkpoint`.
        """
        now = time.time()
        with self.connect() as conn:
            self._append(table, {"entry_type": "checkpoint", "checkpoint_id": checkpoint_id, "step": step,
                                 "data": data, "metadata": metadata, "config": config, "created_at": now}, conn=conn)
            name = ident(table)
            # Prune older intermediate checkpoints (keep last 10 snapshots) to prevent exponential bloat
            conn.execute(
                f"""
                DELETE FROM {name}
                WHERE entry_type = 'checkpoint'
                AND id NOT IN (
                    SELECT id FROM {name}
                    WHERE entry_type = 'checkpoint'
                    ORDER BY step DESC, id DESC
                    LIMIT ?
                )
                """,
                (keep,),
            )
            # Prune orphaned writes
            conn.execute(
                f"""
                DELETE FROM {name}
                WHERE entry_type = 'write'
                AND checkpoint_id NOT IN (
                    SELECT checkpoint_id FROM {name}
                    WHERE entry_type = 'checkpoint'
                )
                """
            )

    def add_write(self, table: str, checkpoint_id: Optional[str], data: bytes) -> None:
        """Saves a pending-write row for `checkpoint_id`."""
        now = time.time()
        self._append(table, {"entry_type": "write", "checkpoint_id": checkpoint_id, "data": data, "created_at": now})

    def checkpoint(self, table: str, checkpoint_id: Optional[str] = None) -> Tuple[Optional[Row], List[Row]]:
        """A checkpoint row of `table` and the `data` of the write rows saved for
        its id; (None, []) if there is none.

        The newest checkpoint, or else the first row SQLite finds with
        `checkpoint_id`, in practice the oldest of the rows saved with it.
        """
        name = ident(table)
        with self.connect() as conn:
            if not table_exists(conn, table):
                return None, []
            if checkpoint_id:
                rows = self.query(f"SELECT * FROM {name} WHERE entry_type = 'checkpoint' AND checkpoint_id = ? LIMIT 1",
                                  (checkpoint_id,), conn=conn)
            else:
                rows = self.query(f"SELECT * FROM {name} WHERE entry_type = 'checkpoint' ORDER BY step DESC, id DESC LIMIT 1",
                                  conn=conn)
            if not rows:
                return None, []
            writes = self.query(f"SELECT data FROM {name} WHERE entry_type = 'write' AND checkpoint_id = ?",
                                (rows[0]["checkpoint_id"],), conn=conn)
        return rows[0], writes

    def checkpoints(self, table: Optional[str] = None) -> List[Row]:
        """The checkpoint rows of `table`, or of every active table if None: newest first within a table."""
        rows: List[Row] = []
        with self.connect() as conn:
            if table is None:
                tables = self.tables(conn=conn)
            else:
                tables = [table] if table_exists(conn, table) else []
            for name in tables:
                rows += self.query(f"SELECT * FROM {ident(name)} WHERE entry_type = 'checkpoint' ORDER BY step DESC, id DESC",
                                   conn=conn)
        return rows

    def delete_last_checkpoint(self, table: str) -> None:
        """Deletes every row saved with the newest checkpoint's id: the checkpoint, its duplicates and its writes."""
        name = ident(table)
        with self.connect() as conn:
            if not table_exists(conn, table):
                return
            rows = self.query(f"SELECT checkpoint_id FROM {name} WHERE entry_type = 'checkpoint' ORDER BY step DESC, id DESC LIMIT 1",
                              conn=conn)
            if rows:
                self.execute(f"DELETE FROM {name} WHERE checkpoint_id = ?", (rows[0]["checkpoint_id"],), conn=conn)

    # -- the ctx_ tables ------------------------------------------------------

    def has_table(self, table: str, *, conn: Optional[sqlite3.Connection] = None) -> bool:
        with self.connect(conn) as c:
            return table_exists(c, table)

    def tables(self, *, include_archived: bool = False, conn: Optional[sqlite3.Connection] = None) -> List[str]:
        """The ctx_ tables in creation order: the active ones, and the archived ones too if `include_archived`.

        Both the prefix and the archive marker are matched literally (in LIKE,
        `_` would match any character).
        """
        with self.connect(conn) as c:
            names = table_names(c, PREFIX)
        return names if include_archived else [name for name in names if ARCHIVED not in name]

    def columns(self, table: str, *, conn: Optional[sqlite3.Connection] = None) -> List[str]:
        """`table`'s column names, in order; none if there is no such table."""
        with self.connect(conn) as c:
            return [row[1] for row in c.execute(f"PRAGMA table_info({ident(table)})")]

    def ensure_table(self, table: str, *, conn: Optional[sqlite3.Connection] = None) -> None:
        """Makes `table` from sessions.sql if it isn't there, and adds the columns an older table lacks.

        An index name another table holds is freed first, so that the index is
        made on this one: tables archived before archiving dropped indexes kept
        the names the session's next table needs. Call it before anything else
        in a transaction: making the table commits whatever is pending.
        """
        names = [index.replace("{{table}}", table) for index in _INDEX.findall(load_schema("sessions"))]
        with self.connect(conn) as c:
            # NOCASE: SQLite compares identifiers case-insensitively (ASCII), so does CREATE INDEX IF NOT EXISTS.
            taken = c.execute(
                f"SELECT name FROM sqlite_master WHERE type = 'index' "
                f"AND name COLLATE NOCASE IN ({placeholders(len(names))}) AND tbl_name <> ? COLLATE NOCASE",
                (*names, table),
            ).fetchall()
            for (index,) in taken:
                c.execute(f"DROP INDEX {ident(index)}")
            self.init(c, table=table)
            present = self.columns(table, conn=c)
            for column, definition in _ADDED_COLUMNS:
                if column not in present:
                    c.execute(f"ALTER TABLE {ident(table)} ADD COLUMN {column} {definition}")

    def archive_table(self, table: str) -> Optional[str]:
        """Archives `table` (see `_archive`): its archived name, or None if there is no such table."""
        ts = int(time.time())
        with self.connect() as conn:
            if not table_exists(conn, table):
                return None
            return self._archive(conn, table, ts)

    def archive_all(self) -> List[Tuple[str, str]]:
        """Archives every active table: each one's (name, archived name)."""
        ts = int(time.time())
        with self.connect() as conn:
            return [(table, self._archive(conn, table, ts)) for table in self.tables(conn=conn)]

    def drop_table(self, table: str) -> None:
        """Drops `table` if it is there, and with it every row of its session, checkpoints and transcript alike."""
        with self.connect() as conn:
            if table_exists(conn, table):
                conn.execute(f"DROP TABLE IF EXISTS {ident(table)}")

    def delete_entries(self, tables: Iterable[str], entry_types: Sequence[str], *,
                       conn: Optional[sqlite3.Connection] = None) -> int:
        """Deletes the rows of `entry_types` from each of `tables` that is there; returns how many.

        Doesn't make or migrate a table: it needs only the entry_type column.
        """
        where, params = where_clause({"entry_type": tuple(entry_types)})
        removed = 0
        with self.connect(conn) as c:
            for table in tables:
                if table_exists(c, table):
                    removed += c.execute(f"DELETE FROM {ident(table)}{where}", params).rowcount
        return removed

    def token_sums(self, since: float, *, conn: Optional[sqlite3.Connection] = None) -> Tuple[int, int]:
        """Input and output tokens of the token rows made at or after `since`, in every ctx_ table, archived ones too."""
        tokens_in = tokens_out = 0
        with self.connect(conn) as c:
            for table in self.tables(include_archived=True, conn=c):
                row = c.execute(
                    f"SELECT COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0) FROM {ident(table)} "
                    "WHERE entry_type = 'token' AND created_at >= ?",
                    (since,),
                ).fetchone()
                tokens_in += row[0]
                tokens_out += row[1]
        return tokens_in, tokens_out

    def vacuum(self) -> None:
        """Reclaims unused space in SQLite database."""
        with self.connect() as conn:
            conn.execute("VACUUM")

    def _append(self, table: str, row: Mapping[str, Any], *, conn: Optional[sqlite3.Connection] = None) -> None:
        """Adds `row` to `table`, making or migrating the table first."""
        with self.connect(conn) as c:
            self.ensure_table(table, conn=c)
            self.insert(table, row, conn=c)

    def _archive(self, conn: sqlite3.Connection, table: str, ts: int) -> str:
        """Renames `table` to `<table>_archived_<ts>` and drops its indexes:
        nothing reads an archived table by index, and the session's next table
        needs their names. Each statement commits on its own."""
        archived = f"{table}{ARCHIVED}{ts}"
        conn.execute(f"ALTER TABLE {ident(table)} RENAME TO {ident(archived)}")
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL", (archived,)
        ).fetchall()
        for (index,) in indexes:
            conn.execute(f"DROP INDEX {ident(index)}")
        return archived
