"""SessionStore (core/storage/sessions.py): the session tables in memory.db, shared with the checkpointer."""
import unittest
import os
import shutil
import tempfile
import json
import sqlite3
import time
from unittest.mock import patch

from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
from core.storage.sessions import ARCHIVED, CHECKPOINT_ENTRIES, SessionStore, sanitize_table_name


def _open_fd_count():
    """Number of file descriptors open in this process, or None if the OS doesn't expose them.

    macOS exposes them at /dev/fd, Linux at /proc/self/fd. Stdlib only - psutil is not a
    declared dependency of this project.
    """
    for fd_dir in ("/dev/fd", "/proc/self/fd"):
        if os.path.isdir(fd_dir):
            try:
                return len(os.listdir(fd_dir))
            except OSError:
                return None
    return None


def _is_connection_open(conn):
    """True if the sqlite connection is still usable (i.e. was never closed)."""
    try:
        conn.execute("SELECT 1")
        return True
    except sqlite3.ProgrammingError:
        return False



class TestSessionStore(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_sessions.db")
        self.store = SessionStore(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_append_and_load_history(self):
        session_id = "session1"
        self.store.append_message(session_id, "user", "hello")
        self.store.append_message(session_id, "bot", "hi")

        history = self.store.load_history(session_id)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["from"], "user")
        self.assertEqual(history[0]["message"], "hello")
        self.assertEqual(history[1]["from"], "bot")
        self.assertEqual(history[1]["message"], "hi")

    def test_append_and_load_token_usage(self):
        session_id = "session1"
        self.store.append_token_usage(session_id, "gemini-pro", 100, 50, 20.0, execution_time=2.45)

        tokens = self.store.load_token_history(session_id)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]["model"], "gemini-pro")
        self.assertEqual(tokens[0]["input_token"], 100)
        self.assertEqual(tokens[0]["output_token"], 50)
        self.assertEqual(tokens[0]["cached_token"], 20.0)
        self.assertEqual(tokens[0]["execution_time"], 2.45)

    def test_archive_session_renames_table(self):
        session_id = "main:discord:general"
        self.store.append_message(session_id, "user", "hello world")
        self.store.append_token_usage(session_id, "gemini-flash", 50, 10, 0.0)

        # Before archive: active table exists
        self.assertIn("ctx_main_discord_general", self.store.list_active_sessions())

        # Archive
        result = self.store.archive_session(session_id)
        self.assertIn("archived to table ctx_main_discord_general_archived_", result)

        # After archive: active table no longer listed as active
        self.assertNotIn("ctx_main_discord_general", self.store.list_active_sessions())

        # Loading history from old session_id returns empty because active table is archived
        history_new = self.store.load_history(session_id)
        self.assertEqual(len(history_new), 0)

        # New message creates fresh active table
        self.store.append_message(session_id, "user", "new conversation turn")
        self.assertIn("ctx_main_discord_general", self.store.list_active_sessions())
        new_hist = self.store.load_history(session_id)
        self.assertEqual(len(new_hist), 1)
        self.assertEqual(new_hist[0]["message"], "new conversation turn")

    def test_archive_all_sessions(self):
        self.store.append_message("session_a", "user", "msg A")
        self.store.append_message("session_b", "user", "msg B")

        active = self.store.list_active_sessions()
        self.assertEqual(len(active), 2)

        res = self.store.archive_all_sessions()
        self.assertIn("Archived", res)

        active_after = self.store.list_active_sessions()
        self.assertEqual(len(active_after), 0)

    def test_archive_session_with_graph_states(self):
        cp = SqliteCheckpointer(self.db_path)

        # 1. Main session
        session_id = "main:discord:general"
        self.store.append_message(session_id, "user", "hello general")

        # 2. Graph states in same session / channel
        cp.put({"configurable": {"thread_id": "graph:content_creation:general"}}, {"id": "cp1"}, {"step": 1}, {})
        cp.put({"configurable": {"thread_id": "graph:coding:general"}}, {"id": "cp2"}, {"step": 1}, {})

        # 3. Graph state in another channel
        cp.put({"configurable": {"thread_id": "graph:content_creation:otherchannel"}}, {"id": "cp3"}, {"step": 1}, {})

        active = self.store.list_active_sessions()
        self.assertIn("ctx_main_discord_general", active)
        self.assertIn("ctx_graph_content_creation_general", active)
        self.assertIn("ctx_graph_coding_general", active)
        self.assertIn("ctx_graph_content_creation_otherchannel", active)

        # Archive session "main:discord:general"
        result = self.store.archive_session(session_id)
        self.assertIn("ctx_main_discord_general_archived_", result)
        self.assertIn("Archived ctx_graph_content_creation_general to ctx_graph_content_creation_general_archived_", result)
        self.assertIn("Archived ctx_graph_coding_general to ctx_graph_coding_general_archived_", result)

        # Verify active sessions
        active_after = self.store.list_active_sessions()
        self.assertNotIn("ctx_main_discord_general", active_after)
        self.assertNotIn("ctx_graph_content_creation_general", active_after)
        self.assertNotIn("ctx_graph_coding_general", active_after)
        self.assertIn("ctx_graph_content_creation_otherchannel", active_after)

        # Verify checkpointer tuple returns None for archived threads
        self.assertIsNone(cp.get_tuple({"configurable": {"thread_id": "graph:content_creation:general"}}))
        self.assertIsNone(cp.get_tuple({"configurable": {"thread_id": "graph:coding:general"}}))
        self.assertIsNotNone(cp.get_tuple({"configurable": {"thread_id": "graph:content_creation:otherchannel"}}))

    def test_archive_all_sessions_includes_all_graph_states(self):
        cp = SqliteCheckpointer(self.db_path)

        self.store.append_message("main:discord:room1", "user", "msg 1")
        cp.put({"configurable": {"thread_id": "graph:content_creation:room1"}}, {"id": "cp1"}, {"step": 1}, {})
        cp.put({"configurable": {"thread_id": "graph:coding:room2"}}, {"id": "cp2"}, {"step": 1}, {})

        active = self.store.list_active_sessions()
        self.assertEqual(len(active), 3)

        res = self.store.archive_all_sessions()
        self.assertIn("Archived ctx_main_discord_room1 to ctx_main_discord_room1_archived_", res)
        self.assertIn("Archived ctx_graph_content_creation_room1 to ctx_graph_content_creation_room1_archived_", res)
        self.assertIn("Archived ctx_graph_coding_room2 to ctx_graph_coding_room2_archived_", res)

        active_after = self.store.list_active_sessions()
        self.assertEqual(len(active_after), 0)
        self.assertIsNone(cp.get_tuple({"configurable": {"thread_id": "graph:content_creation:room1"}}))
        self.assertIsNone(cp.get_tuple({"configurable": {"thread_id": "graph:coding:room2"}}))

    def test_append_list_and_dict_message(self):
        session_id = "session_structured"
        list_msg = [{"type": "text", "text": "hello"}]
        dict_msg = {"key": "val"}
        self.store.append_message(session_id, "user", list_msg)
        self.store.append_message(session_id, "bot", dict_msg)

        history = self.store.load_history(session_id)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["message"], json.dumps(list_msg))
        self.assertEqual(history[1]["message"], json.dumps(dict_msg))

    def test_no_file_descriptor_leak(self):
        # Repeated store operations should close connections and not accumulate open FDs.
        # Every SessionStore call opens its own connection and closes it in a `finally`;
        # if that regresses, every append/load burns db + -wal + -shm handles.
        session_id = "fd_leak_session"

        opened = []
        real_connect = sqlite3.connect

        def tracking_connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            opened.append(conn)
            return conn

        fds_before = _open_fd_count()
        with patch("sqlite3.connect", tracking_connect):
            for i in range(20):
                self.store.append_message(session_id, "user", f"msg {i}")
                self.store.load_history(session_id)
                self.store.append_token_usage(session_id, "gemini-pro", 10, 5, 0.0)
                self.store.load_token_history(session_id)
        fds_after = _open_fd_count()

        # Sanity: the loop really did open connections (4 per iteration)
        self.assertGreaterEqual(len(opened), 80)

        # Every connection the store opened must be closed once its call returns
        still_open = [c for c in opened if _is_connection_open(c)]
        self.assertEqual(
            len(still_open), 0,
            f"{len(still_open)} of {len(opened)} sqlite connections were left open by the session store"
        )

        if fds_before is not None and fds_after is not None:
            self.assertLessEqual(
                fds_after - fds_before, 5,
                f"open file descriptors grew from {fds_before} to {fds_after} across {len(opened)} connections"
            )

    def test_archive_thread_session_does_not_archive_parent_channel(self):
        cp = SqliteCheckpointer(self.db_path)

        main_sess = "main:discord:topic-research"
        thread_sess = "main:discord:topic-research:123456789012345678"

        self.store.append_message(main_sess, "user", "msg in main channel")
        self.store.append_message(thread_sess, "user", "msg in thread")

        cp.put({"configurable": {"thread_id": "graph:coding:topic-research"}}, {"id": "cp_main"}, {"step": 1}, {})
        cp.put({"configurable": {"thread_id": "graph:coding:topic-research:123456789012345678"}}, {"id": "cp_th"}, {"step": 1}, {})

        active = self.store.list_active_sessions()
        self.assertIn("ctx_main_discord_topic_research", active)
        self.assertIn("ctx_main_discord_topic_research_123456789012345678", active)
        self.assertIn("ctx_graph_coding_topic_research", active)
        self.assertIn("ctx_graph_coding_topic_research_123456789012345678", active)

        # Archive only the thread session
        result = self.store.archive_session(thread_sess)
        self.assertIn("ctx_main_discord_topic_research_123456789012345678_archived_", result)
        self.assertIn("Archived ctx_graph_coding_topic_research_123456789012345678 to", result)

        active_after = self.store.list_active_sessions()
        # Thread tables must be archived
        self.assertNotIn("ctx_main_discord_topic_research_123456789012345678", active_after)
        self.assertNotIn("ctx_graph_coding_topic_research_123456789012345678", active_after)

        # Parent channel tables MUST remain active
        self.assertIn("ctx_main_discord_topic_research", active_after)
        self.assertIn("ctx_graph_coding_topic_research", active_after)

        # Main channel history still loads
        main_hist = self.store.load_history(main_sess)
        self.assertEqual(len(main_hist), 1)
        self.assertEqual(main_hist[0]["message"], "msg in main channel")

    def test_the_file_is_made_on_first_use_not_on_construction(self):
        store = SessionStore(os.path.join(self.test_dir, "sub", "memory.db"))
        self.assertFalse(os.path.exists(store.path))
        store.append_message("session1", "user", "hello")
        self.assertTrue(os.path.exists(store.path))
        # Another store on the same file reads it.
        self.assertEqual(SessionStore(store.path).load_history("session1")[0]["message"], "hello")

    def test_the_default_path_is_config_memory_db_path(self):
        with patch.dict(os.environ, {"MEMORY_DB_PATH": self.db_path}):
            self.assertEqual(SessionStore().path, self.db_path)

    def test_a_session_id_with_non_ascii_letters_keeps_them_in_its_table_name(self):
        session_id = "main:discord:café"
        cp = SqliteCheckpointer(self.db_path)
        self.store.append_message(session_id, "user", "bonjour")
        cp.put({"configurable": {"thread_id": session_id}}, {"id": "cp1"}, {"step": 1}, {})

        self.assertIn("ctx_main_discord_café", self.store.list_active_sessions())
        self.assertEqual(self.store.load_history(session_id)[0]["message"], "bonjour")
        self.assertEqual(cp.get_tuple({"configurable": {"thread_id": session_id}}).checkpoint["id"], "cp1")
        self.assertIn("archived to table ctx_main_discord_café_archived_", self.store.archive_session(session_id))


# Each column of a ctx_ table, in order: (name, type, default, not null).
UNIFIED_COLUMNS = [
    ("id", "INTEGER", None, 0), ("entry_type", "TEXT", None, 1), ("checkpoint_id", "TEXT", None, 0),
    ("step", "INTEGER", "-1", 0), ("data", "BLOB", None, 0), ("metadata", "TEXT", None, 0),
    ("config", "TEXT", None, 0), ("parent_config", "TEXT", None, 0), ("from_role", "TEXT", None, 0),
    ("message", "TEXT", None, 0), ("model", "TEXT", None, 0), ("input_tokens", "INTEGER", "0", 0),
    ("output_tokens", "INTEGER", "0", 0), ("cached_tokens", "REAL", "0.0", 0),
    ("execution_time", "REAL", "0.0", 0), ("surface", "TEXT", None, 0), ("created_at", "REAL", None, 1),
]
# Each index, by its suffix after idx_<table>_: its columns.
UNIFIED_INDEXES = {
    "cp_id": ["checkpoint_id"], "cpid": ["entry_type", "checkpoint_id"], "created": ["created_at"],
    "entry_step": ["entry_type", "step", "id"], "type": ["entry_type"],
}
# A table made before execution_time and surface existed.
OLDEST_TABLE = """CREATE TABLE "{table}" (
    id INTEGER PRIMARY KEY AUTOINCREMENT, entry_type TEXT NOT NULL, checkpoint_id TEXT,
    step INTEGER DEFAULT -1, data BLOB, metadata TEXT, config TEXT, parent_config TEXT,
    from_role TEXT, message TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER,
    cached_tokens REAL, created_at REAL NOT NULL
)"""


class TestSessionTables(unittest.TestCase):
    """The ctx_ tables themselves: one definition, migrations, archiving and listing."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_dir)
        self.db_path = os.path.join(self.test_dir, "memory.db")
        self.store = SessionStore(self.db_path)

    def columns(self, table, store=None):
        with (store or self.store).connect() as conn:
            return [(r["name"], r["type"], r["dflt_value"], r["notnull"])
                    for r in conn.execute(f'PRAGMA table_info("{table}")')]

    def indexes(self, table, store=None):
        """`table`'s indexes: {name with `<t>` for the table's own name: columns}."""
        with (store or self.store).connect() as conn:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = ? ORDER BY name", (table,))]
            return {name.replace(table, "<t>", 1): [r["name"] for r in conn.execute(f'PRAGMA index_info("{name}")')]
                    for name in names}

    def unified_indexes(self):
        return {f"idx_<t>_{suffix}": columns for suffix, columns in UNIFIED_INDEXES.items()}

    def test_the_checkpointer_and_the_store_make_the_same_table(self):
        SqliteCheckpointer(self.db_path).put({"configurable": {"thread_id": "a:b"}}, {"id": "cp1"}, {"step": 1}, {})
        self.store.append_message("c:d", "user", "hi")

        self.assertEqual(self.columns("ctx_a_b"), UNIFIED_COLUMNS)
        self.assertEqual(self.columns("ctx_c_d"), UNIFIED_COLUMNS)
        self.assertEqual(self.indexes("ctx_a_b"), self.unified_indexes())
        self.assertEqual(self.indexes("ctx_c_d"), self.unified_indexes())

    def test_an_older_table_gets_the_missing_columns_from_either_writer(self):
        with self.store.connect() as conn:
            for table in ("ctx_old_cp", "ctx_old_store"):
                conn.execute(OLDEST_TABLE.format(table=table))
                conn.execute(f"INSERT INTO \"{table}\" (entry_type, from_role, message, created_at) "
                             "VALUES ('message', 'user', 'kept', 1.0)")

        SqliteCheckpointer(self.db_path).put({"configurable": {"thread_id": "old:cp"}}, {"id": "cp1"}, {"step": 1}, {})
        self.store.append_token_usage("old:store", "m", 1, 2, 0.0, 0.5, surface="voice")

        for table in ("ctx_old_cp", "ctx_old_store"):
            with self.subTest(table=table):
                names = [c[0] for c in self.columns(table)]
                self.assertEqual(names[-3:], ["created_at", "execution_time", "surface"], "added at the end")
                self.assertEqual(sorted(names), sorted(c[0] for c in UNIFIED_COLUMNS))
                self.assertEqual(self.indexes(table), self.unified_indexes())
                self.assertEqual(self.store.load_history(table.removeprefix("ctx_").replace("_", ":"))[0]["message"],
                                 "kept")

    def test_archiving_drops_the_archived_tables_indexes(self):
        archivers = {
            "archive_session": lambda store, cp, session: store.archive_session(session),
            "archive_all_sessions": lambda store, cp, session: store.archive_all_sessions(),
            "archive_thread": lambda store, cp, session: cp.archive_thread(session),
            "archive_all": lambda store, cp, session: cp.archive_all(),
        }
        session = "main:discord:general"
        table = sanitize_table_name(session)
        for name, archive in archivers.items():
            with self.subTest(name):
                store = SessionStore(os.path.join(self.test_dir, f"{name}.db"))
                cp = SqliteCheckpointer(store.path)
                store.append_message(session, "user", "before")
                cp.put({"configurable": {"thread_id": session}}, {"id": "cp1"}, {"step": 1}, {})

                archive(store, cp, session)

                archived = [t for t in store.tables(include_archived=True) if t.startswith(table + ARCHIVED)]
                self.assertEqual(len(archived), 1)
                self.assertEqual(self.indexes(archived[0], store), {})
                self.assertEqual(store.count(archived[0]), 2, "the rows are kept")
                # The session's next table gets every index.
                store.append_message(session, "user", "after")
                self.assertEqual(self.indexes(table, store), self.unified_indexes())

    def test_a_table_takes_back_index_names_an_older_archive_holds(self):
        """Archives made before archiving dropped indexes kept their names, and the
        session's next table silently got none. Its next write heals it."""
        cases = {
            # the next table made by the new code
            "made now": ("main:discord:general", None),
            # the next table made by the old code, without indexes, then written to
            "made without indexes": ("main:discord:lifestyle-perk", "bare"),
            # names differ in case only: SQLite still calls them the same
            "case": ("graph:Coding:general", "lower"),
        }
        sibling = "main:discord:general:123456789012345678"  # a table whose name starts with the same text
        self.store.append_message(sibling, "user", "thread")
        for name, (session, variant) in cases.items():
            with self.subTest(name):
                table = sanitize_table_name(session)
                self.store.append_message(session, "user", "old")
                with self.store.connect() as conn:  # archive the old way: rename only
                    conn.execute(f'ALTER TABLE "{table}" RENAME TO "{table}{ARCHIVED}1"')
                self.assertEqual(len(self.indexes(f"{table}{ARCHIVED}1")), 5)
                if variant == "bare":
                    with self.store.connect() as conn:
                        conn.execute(OLDEST_TABLE.format(table=table))
                    self.assertEqual(self.indexes(table), {})
                if variant == "lower":
                    session, table = session.lower(), table.lower()

                SqliteCheckpointer(self.db_path).put({"configurable": {"thread_id": session}}, {"id": "cp1"}, {"step": 1}, {})

                self.assertEqual(self.indexes(table), self.unified_indexes())
                self.assertEqual(self.indexes(f"{sanitize_table_name(cases[name][0])}{ARCHIVED}1"), {})
        self.assertEqual(self.indexes(sanitize_table_name(sibling)), self.unified_indexes(), "a sibling keeps its own")

    def test_listing_matches_the_prefix_and_the_archive_marker_literally(self):
        with self.store.connect() as conn:
            for table in ("ctxXa", "ctx_main_discord_xarchivedy", "ctx_a_archived_1", "jobs"):
                conn.execute(f'CREATE TABLE "{table}" (entry_type TEXT)')

        self.assertEqual(self.store.tables(), ["ctx_main_discord_xarchivedy"],
                         "LIKE 'ctx_%' listed ctxXa, and NOT LIKE '%_archived_%' hid ...xarchivedy")
        self.assertEqual(self.store.list_active_sessions(), ["ctx_main_discord_xarchivedy"])
        self.assertEqual(self.store.tables(include_archived=True), ["ctx_main_discord_xarchivedy", "ctx_a_archived_1"])

    def test_delete_entries_needs_no_schema(self):
        with self.store.connect() as conn:
            conn.execute('CREATE TABLE "ctx_minimal" (entry_type TEXT, data TEXT)')
            conn.executemany('INSERT INTO "ctx_minimal" VALUES (?, ?)',
                             [("checkpoint", "1"), ("write", "2"), ("message", "3"), ("token", "4")])

        self.assertEqual(self.store.delete_entries(["ctx_minimal", "ctx_missing"], CHECKPOINT_ENTRIES), 2)
        with self.store.connect() as conn:
            self.assertEqual(sorted(r[0] for r in conn.execute('SELECT entry_type FROM "ctx_minimal"')),
                             ["message", "token"])
        self.assertEqual(self.store.columns("ctx_minimal"), ["entry_type", "data"], "not migrated")
        self.assertFalse(self.store.has_table("ctx_missing"), "not made")

    def test_token_sums_count_archived_tables_too(self):
        self.store.append_token_usage("a", "m", 100, 20, 0.0)
        self.store.archive_session("a")
        self.store.append_token_usage("a", "m", 5, 1, 0.0)
        self.store.append_message("a", "user", "not a token row")

        self.assertEqual(self.store.token_sums(0), (105, 21))
        self.assertEqual(self.store.token_sums(time.time() + 60), (0, 0))


if __name__ == "__main__":
    unittest.main()
