"""memory.db's session tables: one `ctx_<session>` table per session or LangGraph thread.

Two writers share a session's table (schemas/sessions.sql):

- SqliteCheckpointer (core/knowledge/memory/sqlite_checkpointer.py) keeps
  LangGraph's state in `checkpoint` and `write` rows. It pickles them; the
  checkpoint methods here only store and find the blobs.
- The session store proper adds the readable transcript (`message` rows),
  token use (`token`), voice turn traces (`turn_trace`), and what the voice
  room did on its own there -- its greeting, a move of its focus (`room_event`).

A table is made by its first write. One made by older code keeps its column
order and gets the columns it lacks, so rows are read by column name. `!new`
archives a session by renaming its tables to `<table>_archived_<epoch>`; they
are kept for the token reports, and no session reads them again.

At startup, the tables nothing running reads (the archived ones, and finished
jobs' sessions) move to archive.db beside memory.db, under the same names
(`archive_inactive`). memory.db keeps only what's live: every connection loads
its whole schema, and the archived tables were 92% of the file.
"""
import json
import os
import re
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.storage.base import WAL_PRAGMAS, Row, Store, load_schema
from core.storage.util import ident, placeholders, table_exists, table_names, where_clause
from core.config.settings import Config

PREFIX = "ctx_"
# What archiving puts in a table's name: `<table>_archived_<epoch>`.
ARCHIVED = "_archived_"
# The file beside memory.db that archive_inactive() moves tables into.
ARCHIVE_FILE = "archive.db"
# A job's id (ExecutionContext.new_job_id): its session is `<agent>:job:<id>`, one table per job.
_JOB_ID = re.compile(r"[0-9a-f]{8}")
# A CREATE TABLE statement's head, up to the table's name, as sqlite_master keeps it.
_CREATE_TABLE = re.compile(r'^\s*CREATE\s+TABLE\s+(?:"(?:[^"]|"")*"|`[^`]*`|\[[^\]]*\]|[^\s(]+)', re.IGNORECASE)
# The rows LangGraph replays as a session's history. Message and token rows are the rest.
CHECKPOINT_ENTRIES = ("checkpoint", "write")
# Checkpoints a table keeps: saving one prunes the older ones and their writes.
KEEP_CHECKPOINTS = 10
# How old a write with no checkpoint must be before a save prunes it: the saver's
# writer thread can save a write just before its checkpoint (core/knowledge/memory/sqlite_checkpointer.py).
ORPHAN_WRITE_GRACE_S = 600
# Columns added after the first tables were made, and their definitions.
# Rows written before the column existed stay NULL: their surface is unknown, not "text".
_ADDED_COLUMNS = (("execution_time", "REAL DEFAULT 0.0"), ("surface", "TEXT"))
_INDEX = re.compile(r'CREATE INDEX IF NOT EXISTS "([^"]+)"')
# octet_length() gives a value's size from the row header, without reading it (SQLite 3.43+).
_HAS_OCTET_LENGTH = sqlite3.sqlite_version_info >= (3, 43, 0)


def sanitize_table_name(session_id: str) -> str:
    """Sanitizes a session/context ID into a valid SQLite table name.

    Lossy: `a:b`, `a-b` and `a_b` share a table.
    """
    clean = "".join(c if c.isalnum() or c == "_" else "_" for c in session_id)
    return f"ctx_{clean}"


def _table_named(conn: sqlite3.Connection, schema: str, table: str) -> Optional[str]:
    """The name `schema` (main or archive) keeps `table` under, if it has it: SQLite matches
    table names case-insensitively."""
    row = conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table' AND name = ? COLLATE NOCASE",
                       (table,)).fetchone()
    return row[0] if row else None


def _same_rows(conn: sqlite3.Connection, table: str, there: str, rows: int) -> bool:
    """Whether the archive's `there` holds just what main's `table` does: its `rows` rows, no other."""
    mine, theirs = f"main.{ident(table)}", f"archive.{ident(there)}"
    if conn.execute(f"SELECT count(*) FROM {theirs}").fetchone()[0] != rows:
        return False
    try:
        return not any(conn.execute(f"SELECT EXISTS (SELECT * FROM {a} EXCEPT SELECT * FROM {b})").fetchone()[0]
                       for a, b in ((mine, theirs), (theirs, mine)))
    except sqlite3.OperationalError:  # not even the same columns
        return False


def _free_name(conn: sqlite3.Connection, table: str) -> str:
    """`table` with the first of `_2`, `_3`... that names no table in main or the archive."""
    n = 2
    while _table_named(conn, "main", f"{table}_{n}") or _table_named(conn, "archive", f"{table}_{n}"):
        n += 1
    return f"{table}_{n}"


class SessionStore(Store):
    """The session tables in memory.db, at `Config().memory_db_path` unless a path is given.

    The methods that take a session id find its table with sanitize_table_name;
    the others take table names. Each opens its own connection unless `conn=`
    is given. A table is made by its first write (`ensure_table`), never on
    connect: reading a table that isn't there gives nothing back, and a
    hand-built table is left as it is.
    """

    schema = ("sessions",)
    pragmas = WAL_PRAGMAS
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
        """Stores one turn's latency trace (see core.runtime.observability.turn_trace) beside its token rows.

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

    def append_room_event(self, session_id: str, event: Dict[str, Any], surface: Optional[str] = "voice") -> str:
        """Stores something the voice room did on its own -- the greeting when
        you join, a move of its focus -- as JSON in `message`, beside the
        session's turns. These lines run no turn, so without a row of their
        own memory.db never showed when you joined or what you heard first.
        `execution_time` is how long the room waited before speaking (seconds;
        NULL when it didn't wait), the figure a lost greeting is checked by."""
        table_name = sanitize_table_name(session_id)
        waited_ms = event.get("waited_ms")
        self._append(table_name, {"entry_type": "room_event", "message": json.dumps(event, default=str),
                                  "execution_time": None if waited_ms is None else waited_ms / 1000.0,
                                  "surface": surface, "created_at": time.time()})
        return f"Appended room event to {session_id}"

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

    def recent_history(self, session_id: str, limit: int, max_bytes: Optional[int] = None) -> List[Dict[str, Any]]:
        """The last `limit` transcript rows, oldest first, shaped as `load_history`'s.

        Reads only those rows, where load_history reads every one. With
        `max_bytes`, a row longer than that is left out: a tool's output logged
        whole can run to many megabytes. On an SQLite with octet_length() (3.43+)
        such a row is skipped without being read; an older one reads it and
        drops it after.
        """
        table_name = sanitize_table_name(session_id)
        skip_unread = max_bytes is not None and _HAS_OCTET_LENGTH
        with self.connect() as conn:
            if not table_exists(conn, table_name):
                return []
            rows = self.query(
                f"SELECT from_role, message, created_at FROM {ident(table_name)} WHERE entry_type = 'message'"
                f"{' AND octet_length(message) <= ?' if skip_unread else ''} ORDER BY id DESC LIMIT ?",
                [max_bytes, limit] if skip_unread else [limit],
                conn=conn,
            )
        data = [{"from": r["from_role"], "message": r["message"], "ts": int(r["created_at"])} for r in reversed(rows)]
        if max_bytes is not None and not skip_unread:
            data = [d for d in data if len((d["message"] or "").encode("utf-8")) <= max_bytes]
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
        checkpoint under its own id): see `checkpoint`. A write whose checkpoint
        isn't saved is pruned only once it's ORPHAN_WRITE_GRACE_S old, as its
        checkpoint may still be on its way.
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
            # Prune orphaned writes, past the grace period
            conn.execute(
                f"""
                DELETE FROM {name}
                WHERE entry_type = 'write'
                AND created_at < ?
                AND checkpoint_id NOT IN (
                    SELECT checkpoint_id FROM {name}
                    WHERE entry_type = 'checkpoint'
                )
                """,
                (now - ORPHAN_WRITE_GRACE_S,),
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

    def job_tables(self, agent_ids: Iterable[str], *, conn: Optional[sqlite3.Connection] = None) -> List[str]:
        """The active tables of these agents' job sessions (`<agent>:job:<8 hex>`), one per job.

        Matched by that exact shape: no other session id makes such a name.
        """
        heads = {sanitize_table_name(f"{agent_id}:job:") for agent_id in agent_ids}
        return [name for name in self.tables(conn=conn) if name[:-8] in heads and _JOB_ID.fullmatch(name[-8:])]

    def tables_with_archive(self, conn: sqlite3.Connection) -> List[str]:
        """Every ctx_ table `conn` can read, archived ones included, and archive_store()'s too.

        Attaches archive.db to `conn` (as `archive`) unless it is already, so a
        query naming a table unqualified finds it in either file. A table in
        both, as an interrupted move leaves it, is listed once.
        """
        names = self.tables(include_archived=True, conn=conn)
        archive = self.archive_store()
        if not archive.exists():
            return names
        if "archive" not in {row[1] for row in conn.execute("PRAGMA database_list")}:
            conn.execute("ATTACH DATABASE ? AS archive", (archive.path,))
        seen = set(names)
        return names + [name for name in table_names(conn, PREFIX, schema="archive") if name not in seen]

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

    # -- the archive file (archive.db, beside this one) ------------------------

    def archive_store(self) -> "SessionStore":
        """The archive beside this file (ARCHIVE_FILE), where archive_inactive() moves tables, under their names."""
        return SessionStore(os.path.join(os.path.dirname(self.path), ARCHIVE_FILE))

    def archive_inactive(self, agent_ids: Iterable[str]) -> Tuple[int, int]:
        """Moves the tables nothing running reads into archive_store(): every archived one (`!new`)
        and these agents' job sessions. Then VACUUMs this file if over a quarter of it is free,
        as the first move leaves it. Returns the (tables, rows) moved.

        For startup, before anything holds this file or runs a job: no job
        outlives its process, so every job session is finished by then. `!job`
        finds a moved one's log in the archive.
        """
        started = time.monotonic()
        with self.connect() as conn:
            tables = [t for t in self.tables(include_archived=True, conn=conn) if ARCHIVED in t]
            tables += self.job_tables(agent_ids, conn=conn)
        moved, rows = self.move_to_archive(tables)
        if moved:
            print(f"[storage] Moved {moved} tables ({rows:,} rows) to {self.archive_store().path} "
                  f"in {time.monotonic() - started:.1f} s")
        started, size = time.monotonic(), os.path.getsize(self.path)
        if self.vacuum_if_sparse():
            print(f"[storage] Compacted {self.path} from {size / 1e6:.1f} to {os.path.getsize(self.path) / 1e6:.1f} MB "
                  f"in {time.monotonic() - started:.1f} s")
        return moved, rows

    def move_to_archive(self, tables: Sequence[str]) -> Tuple[int, int]:
        """Moves `tables` into archive_store(), each under its own name; returns the (tables, rows) moved.

        Stopping at any point loses nothing: every table is copied, its rows
        counted, and the archive synced to disk before any table leaves this
        file, and nothing in the archive is dropped. A crash leaves at worst a
        table in both, which the next call finishes moving.
        """
        if not tables:
            return 0, 0
        copied: List[Tuple[str, int]] = []
        with self._open(init=False) as conn:
            conn.isolation_level = None  # explicit transactions: ATTACH can't run inside one
            conn.execute("ATTACH DATABASE ? AS archive", (self.archive_store().path,))
            try:
                # NORMAL: a commit doesn't sync (65 ms on the NAS, per table); the checkpoint below does.
                for pragma in ("journal_mode = WAL", "synchronous = NORMAL"):
                    conn.execute(f"PRAGMA archive.{pragma}").fetchall()
                for table in tables:
                    rows = self._copy_to_archive(conn, table)
                    if rows is not None:
                        copied.append((table, rows))
                # Syncs the archive's WAL, folds it all in, then syncs the archive: every copy on disk.
                busy, frames, folded = conn.execute("PRAGMA archive.wal_checkpoint(FULL)").fetchone()
                if busy or frames != folded:
                    raise sqlite3.OperationalError(f"the archive's WAL wasn't folded in: {folded} of {frames} frames")
                conn.execute("BEGIN")
                for table, _ in copied:
                    conn.execute(f"DROP TABLE main.{ident(table)}")
                conn.execute("COMMIT")
            finally:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                conn.execute("DETACH DATABASE archive")
        return len(copied), sum(rows for _, rows in copied)

    def _copy_to_archive(self, conn: sqlite3.Connection, table: str) -> Optional[int]:
        """Copies `table` into the attached archive, with its own CREATE statement; its row
        count, or None if the table is gone.

        A table the archive already has under that name is either this one,
        copied by a move that stopped before dropping it here, and kept as it
        is, or another that got the same name (a job id drawn twice), which is
        renamed out of the way.
        """
        found = conn.execute("SELECT sql FROM main.sqlite_master WHERE type = 'table' AND name = ?",
                             (table,)).fetchone()
        if found is None:
            return None
        head = _CREATE_TABLE.match(found[0])
        if head is None:
            raise sqlite3.DatabaseError(f"{table}: its CREATE statement isn't one this can copy: {found[0][:80]!r}")
        name = ident(table)
        rows = conn.execute(f"SELECT count(*) FROM main.{name}").fetchone()[0]
        there = _table_named(conn, "archive", table)
        if there is not None and _same_rows(conn, table, there, rows):
            return rows  # copied by a move that stopped before dropping it here
        conn.execute("BEGIN")
        if there is not None:
            conn.execute(f"ALTER TABLE archive.{ident(there)} RENAME TO {ident(_free_name(conn, there))}")
        conn.execute(f"CREATE TABLE archive.{name}{found[0][head.end():]}")
        conn.execute(f"INSERT INTO archive.{name} SELECT * FROM main.{name}")
        if conn.execute(f"SELECT count(*) FROM archive.{name}").fetchone()[0] != rows:
            raise sqlite3.DatabaseError(f"{table}: the archive's copy doesn't have all {rows} rows")
        conn.execute("COMMIT")
        return rows

    def vacuum_if_sparse(self, share: float = 0.25) -> bool:
        """VACUUMs the file when more than `share` of its pages are free; True if it did."""
        with self.connect() as conn:
            free = conn.execute("PRAGMA freelist_count").fetchone()[0]
            pages = conn.execute("PRAGMA page_count").fetchone()[0]
        if not pages or free <= pages * share:
            return False
        self.vacuum()
        return True

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
