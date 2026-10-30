"""The Store base class (core/storage/base.py) and its statement helpers (util.py)."""
import os
import sqlite3
import tempfile
import unittest
from enum import Enum
from pathlib import Path
from unittest.mock import patch

from core.storage import base
from core.storage.base import Store, StoreMissing, apply_schema, load_schema
from core.storage.util import decode_json, ident, table_names, where_clause

NOTES = """CREATE TABLE IF NOT EXISTS notes (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    topic  TEXT NOT NULL UNIQUE,
    body   TEXT,
    kind   TEXT
);
"""


class Kind(str, Enum):
    IDEA = "IDEA"
    TODO = "TODO"


class SchemaDir(unittest.TestCase):
    """A temporary schemas/ folder holding `notes.sql`, plus whatever a test adds."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.schemas = self.root / "schemas"
        self.schemas.mkdir()
        self.write_schema("notes", NOTES)
        load_schema.cache_clear()
        self.addCleanup(load_schema.cache_clear)
        patcher = patch.object(base, "SCHEMAS_DIR", self.schemas)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_schema(self, name, sql):
        (self.schemas / f"{name}.sql").write_text(sql, encoding="utf-8")

    def store(self, cls=None, name="sub/notes.db"):
        cls = cls or Notes
        return cls(str(self.root / name))


class Notes(Store):
    schema = ("notes",)


class TestConnections(SchemaDir):

    def test_connect_creates_the_folder_the_file_and_the_schema(self):
        store = self.store()
        self.assertFalse(store.exists())
        with store.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM notes").fetchone()[0], 0)
        self.assertTrue(store.exists())

    def test_a_body_that_succeeds_is_committed_and_one_that_raises_is_not(self):
        store = self.store()
        with store.connect() as conn:
            conn.execute("INSERT INTO notes (topic) VALUES ('kept')")
        with self.assertRaises(RuntimeError):
            with store.connect() as conn:
                conn.execute("INSERT INTO notes (topic) VALUES ('dropped')")
                raise RuntimeError("boom")
        self.assertEqual([r["topic"] for r in store.select("notes")], ["kept"])

    def test_rows_are_addressable_by_column_name(self):
        store = self.store()
        store.insert("notes", {"topic": "t", "body": "b"})
        self.assertEqual(store.select_one("notes")["body"], "b")

    def test_read_only_never_creates_anything(self):
        store = self.store()
        with self.assertRaises(StoreMissing):
            with store.read_only():
                pass
        self.assertFalse(os.path.exists(os.path.dirname(store.path)), "not even the folder")
        self.assertTrue(issubclass(StoreMissing, FileNotFoundError))

    def test_read_only_reads_but_cannot_write(self):
        store = self.store()
        store.insert("notes", {"topic": "t"})
        with store.read_only() as conn:
            self.assertEqual(conn.execute("SELECT topic FROM notes").fetchone()["topic"], "t")
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO notes (topic) VALUES ('x')")

    def test_pragmas_run_on_every_read_write_connection(self):
        class Wal(Notes):
            pragmas = ("journal_mode = WAL",)
        store = self.store(Wal)
        with store.connect() as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_the_default_path_is_resolved_on_every_call(self):
        where = {"path": str(self.root / "a.db")}

        class Moving(Notes):
            def default_path(self):
                return where["path"]
        store = Moving()
        self.assertEqual(store.path, str(self.root / "a.db"))
        where["path"] = str(self.root / "b.db")
        self.assertEqual(store.path, str(self.root / "b.db"))

    def test_a_store_without_a_default_path_needs_one(self):
        with self.assertRaises(NotImplementedError):
            Notes().path

    def test_a_home_relative_path_is_expanded(self):
        self.assertEqual(Notes("~/x.db").path, os.path.expanduser("~/x.db"))


class TestHoldOpen(SchemaDir):
    """hold_open(): one idle connection, so the stores' own closes don't fold a WAL file's log in."""

    class Wal(Notes):
        pragmas = ("journal_mode = WAL", "synchronous = NORMAL")

    def wal_store(self, hold=True):
        store = self.store(self.Wal)
        self.addCleanup(store.release_hold)
        if hold:
            self.assertTrue(store.hold_open())
        return store

    def wal_bytes(self, store):
        wal = store.path + "-wal"
        return os.path.getsize(wal) if os.path.exists(wal) else None

    def test_with_none_held_the_last_close_folds_the_wal_in_and_deletes_it(self):
        store = self.wal_store(hold=False)
        store.insert("notes", {"topic": "t"})
        self.assertIsNone(self.wal_bytes(store))

    def test_held_the_wal_outlives_the_stores_closes(self):
        store = self.wal_store()
        store.insert("notes", {"topic": "t"})
        self.assertGreater(self.wal_bytes(store), 0)
        self.assertEqual([r["topic"] for r in self.store(self.Wal).select("notes")], ["t"])

    def test_it_keeps_no_read_open_so_a_full_checkpoint_completes(self):
        store = self.wal_store(hold=False)
        store.insert("notes", {"topic": "before"})  # held like memory.db: a file with data
        self.assertTrue(store.hold_open())
        for i in range(5):
            store.insert("notes", {"topic": f"t{i}"})
        with store.connect() as conn:
            busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
        self.assertEqual(busy, 0, "a read left open on the held connection would make it busy")
        self.assertEqual(self.wal_bytes(store), 0)

    def test_a_file_is_held_once(self):
        self.wal_store()
        self.assertFalse(self.store(self.Wal).hold_open(), "another store on the same file shares the hold")

    def test_released_the_last_close_folds_the_wal_in_again(self):
        store = self.wal_store()
        store.insert("notes", {"topic": "t"})
        store.release_hold()
        self.assertIsNone(self.wal_bytes(store))
        store.insert("notes", {"topic": "u"})
        self.assertIsNone(self.wal_bytes(store))
        self.assertTrue(store.hold_open(), "and it can be held again")


class TestSchema(SchemaDir):

    def test_a_store_that_doesnt_init_on_connect_opens_whatever_is_there(self):
        class Explicit(Notes):
            init_on_connect = False
        store = self.store(Explicit)
        with store.connect() as conn:
            conn.execute("CREATE TABLE notes (topic TEXT)")  # a hand-built minimal table
        with store.connect() as conn:
            self.assertEqual([r[1] for r in conn.execute("PRAGMA table_info(notes)")], ["topic"])

    def test_init_creates_the_schema_and_is_idempotent(self):
        class Explicit(Notes):
            init_on_connect = False
        store = self.store(Explicit)
        store.init()
        store.init()
        with store.connect() as conn:
            store.init(conn)
            self.assertIn("body", [r[1] for r in conn.execute("PRAGMA table_info(notes)")])

    def test_init_fills_a_per_table_schema(self):
        self.write_schema("per_table", 'CREATE TABLE IF NOT EXISTS "{{table}}" (x);')

        class PerTable(Store):
            schema = ("per_table",)
            init_on_connect = False
        store = self.store(PerTable)
        with store.connect() as conn:
            store.init(conn, table="ctx_a")
            store.init(conn, table="ctx_b")
            self.assertEqual(table_names(conn, "ctx_"), ["ctx_a", "ctx_b"])

    def test_an_optional_schema_that_fails_is_skipped(self):
        self.write_schema("broken", "CREATE VIRTUAL TABLE nope USING no_such_module(a);")

        class Optional(Notes):
            optional_schema = ("broken",)
        store = self.store(Optional)
        with patch("builtins.print") as printed:
            store.insert("notes", {"topic": "t"})
        self.assertEqual(store.count("notes"), 1)
        self.assertIn("broken.sql", printed.call_args[0][0])

    def test_a_placeholder_takes_a_plain_identifier(self):
        self.write_schema("per_table", 'CREATE TABLE IF NOT EXISTS "{{table}}" (x);\n'
                                       'CREATE INDEX IF NOT EXISTS "idx_{{table}}_x" ON "{{table}}" (x);')
        conn = sqlite3.connect(":memory:")
        apply_schema(conn, "per_table", table="ctx_a")
        apply_schema(conn, "per_table", table="ctx_a")
        self.assertEqual(table_names(conn), ["ctx_a"])
        self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchone()[0],
                         "idx_ctx_a_x")
        with self.assertRaises(ValueError):
            apply_schema(conn, "per_table", table='x"; DROP TABLE ctx_a; --')
        with self.assertRaises(KeyError):
            apply_schema(conn, "per_table")


class TestCrud(SchemaDir):

    def setUp(self):
        super().setUp()
        self.notes = self.store()

    def topics(self, **kwargs):
        return [r["topic"] for r in self.notes.select("notes", order_by="id", **kwargs)]

    def test_insert_returns_the_rowid_and_none_when_ignored(self):
        self.assertEqual(self.notes.insert("notes", {"topic": "a"}), 1)
        self.assertEqual(self.notes.insert("notes", {"topic": "b"}), 2)
        self.assertIsNone(self.notes.insert("notes", {"topic": "a"}, or_="IGNORE"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.notes.insert("notes", {"topic": "a"})
        replaced = self.notes.insert("notes", {"topic": "a", "body": "new"}, or_="REPLACE")
        self.assertEqual(self.notes.select_one("notes", {"topic": "a"})["id"], replaced)
        self.assertEqual(self.notes.select_one("notes", {"topic": "a"})["body"], "new")
        with self.assertRaises(ValueError):
            self.notes.insert("notes", {"topic": "c"}, or_="ABORT; DROP TABLE notes")

    def test_upsert_adds_then_updates_only_the_columns_given(self):
        self.notes.upsert("notes", {"topic": "a", "body": "one", "kind": Kind.IDEA}, "topic")
        self.notes.upsert("notes", {"topic": "a", "body": "two"}, "topic")
        row = self.notes.select_one("notes", {"topic": "a"})
        self.assertEqual((row["body"], row["kind"], self.notes.count("notes")), ("two", "IDEA", 1))

    def test_where_matches_null_lists_and_values(self):
        for topic, kind in (("a", Kind.IDEA), ("b", Kind.TODO), ("c", None)):
            self.notes.insert("notes", {"topic": topic, "kind": kind})
        self.assertEqual(self.topics(where={"kind": Kind.TODO}), ["b"])
        self.assertEqual(self.topics(where={"kind": None}), ["c"])
        self.assertEqual(self.topics(where={"topic": ["a", "c"]}), ["a", "c"])
        self.assertEqual(self.topics(where={"topic": []}), [])
        self.assertEqual(self.topics(where={"topic": ("a", "b"), "kind": "IDEA"}), ["a"])
        self.assertEqual(self.topics(limit=2), ["a", "b"])
        self.assertEqual([r["topic"] for r in self.notes.select("notes", order_by="topic DESC")], ["c", "b", "a"])
        self.assertEqual(self.notes.select("notes", {"topic": "a"}, columns=("kind",))[0].keys(), ["kind"])
        self.assertIsNone(self.notes.select_one("notes", {"topic": "zzz"}))

    def test_update_and_delete_report_how_many_rows_changed(self):
        for topic in "abc":
            self.notes.insert("notes", {"topic": topic})
        self.assertEqual(self.notes.update("notes", {"body": "x"}, {"topic": ["a", "b"]}), 2)
        self.assertEqual(self.notes.count("notes", {"body": "x"}), 2)
        self.assertEqual(self.notes.delete("notes", {"topic": "a"}), 1)
        self.assertEqual(self.notes.delete("notes", {"topic": "a"}), 0)
        self.assertEqual(self.topics(), ["b", "c"])

    def test_update_and_delete_need_a_condition(self):
        self.notes.insert("notes", {"topic": "a"})
        with self.assertRaises(ValueError):
            self.notes.update("notes", {"body": "x"}, {})
        with self.assertRaises(ValueError):
            self.notes.delete("notes", {})
        self.assertEqual(self.notes.execute("DELETE FROM notes"), 1)

    def test_query_and_execute_take_parameters(self):
        self.notes.insert("notes", {"topic": "a", "kind": Kind.IDEA})
        self.assertEqual(self.notes.query("SELECT topic FROM notes WHERE kind = ?", (Kind.IDEA,))[0]["topic"], "a")
        self.assertEqual(self.notes.execute("UPDATE notes SET body = ? WHERE topic = ?", ("b", "a")), 1)

    def test_connect_given_a_connection_joins_it(self):
        with self.notes.connect() as outer:
            with self.notes.connect(outer) as inner:
                self.assertIs(inner, outer)
                inner.execute("INSERT INTO notes (topic) VALUES ('a')")
            self.assertTrue(outer.in_transaction, "joining neither commits nor closes")
        self.assertEqual(self.notes.count("notes"), 1)

    def test_helpers_given_a_connection_share_its_transaction(self):
        with self.assertRaises(RuntimeError):
            with self.notes.connect() as conn:
                self.notes.insert("notes", {"topic": "a"}, conn=conn)
                self.assertEqual(self.notes.count("notes", conn=conn), 1)
                raise RuntimeError("boom")
        self.assertEqual(self.notes.count("notes"), 0)

    def test_names_must_be_plain_identifiers(self):
        with self.assertRaises(ValueError):
            self.notes.select("notes; DROP TABLE notes")
        with self.assertRaises(ValueError):
            self.notes.select("notes", {"topic = topic OR 1": 1})


class TestUtil(unittest.TestCase):

    def test_ident_quotes_plain_identifiers_only(self):
        self.assertEqual(ident("ctx_a1"), '"ctx_a1"')
        self.assertEqual(ident("ctx_café"), '"ctx_café"', "sanitized session names keep any letter")
        for bad in ("", "1a", "a b", 'a"b', "a-b", "a;b", "é\""):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                ident(bad)

    def test_where_clause(self):
        self.assertEqual(where_clause(None), ("", []))
        self.assertEqual(where_clause({"a": 1, "b": None, "c": [2, 3]}),
                         (' WHERE "a" = ? AND "b" IS NULL AND "c" IN (?, ?)', [1, 2, 3]))

    def test_decode_json_decodes_json_text_and_leaves_the_rest(self):
        row = {"tags": '["a", "b"]', "aliases": "not json", "empty": "", "n": 3}
        self.assertIs(decode_json(row, ("tags", "aliases", "empty", "n", "missing")), row)
        self.assertEqual(row, {"tags": ["a", "b"], "aliases": "not json", "empty": "", "n": 3})

    def test_table_names_match_the_prefix_literally(self):
        conn = sqlite3.connect(":memory:")
        for name in ("ctx_a", "ctxXb", "jobs"):
            conn.execute(f"CREATE TABLE {name} (x)")
        self.assertEqual(table_names(conn, "ctx_"), ["ctx_a"], "LIKE 'ctx_%' would also match ctxXb")
        self.assertEqual(table_names(conn), ["ctx_a", "ctxXb", "jobs"])


if __name__ == "__main__":
    unittest.main()
