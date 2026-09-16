import unittest
import os
import shutil
import tempfile
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from core.knowledge.memory.flat_file_session_store import FlatFileSessionStore
from core.knowledge.memory.sqlite_session_store import SqliteSessionStore

class TestFlatFileSessionStore(unittest.TestCase):
    """
    FlatFileSessionStore is a backwards-compatibility shim (core/knowledge/memory/flat_file_session_store.py):
    a constructor-only subclass of SqliteSessionStore whose *only* own behaviour is mapping the legacy
    `sessions_dir=` argument onto <sessions_dir>/memory.db.

    Message/token/archive behaviour is inherited verbatim and is already covered by TestSqliteSessionStore
    in test_sqlite_session_store.py, so this file tests only the shim's own contract.
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_sessions_dir_argument_maps_to_memory_db_inside_directory(self):
        store = FlatFileSessionStore(sessions_dir=self.test_dir)
        expected_db = os.path.join(self.test_dir, "memory.db")
        self.assertEqual(store.db_path, expected_db)

        # SqliteSessionStore only creates the file lazily on first write; writing must hit the mapped path.
        store.append_message("session1", "user", "hello")
        self.assertTrue(
            os.path.exists(expected_db),
            f"expected the shim to write {expected_db}, dir contains {os.listdir(self.test_dir)}"
        )

    def test_db_path_argument_takes_precedence_over_sessions_dir(self):
        explicit_db = os.path.join(self.test_dir, "explicit.db")
        store = FlatFileSessionStore(sessions_dir=self.test_dir, db_path=explicit_db)
        self.assertEqual(store.db_path, explicit_db)

        store.append_message("session1", "user", "hello")
        self.assertTrue(os.path.exists(explicit_db))
        # The sessions_dir mapping must not also fire when an explicit db_path was given.
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, "memory.db")))

    def test_is_a_working_sqlite_session_store_subclass(self):
        # Inherited behaviour is tested next door; here we only prove the subclass wiring is real
        # and that inherited I/O actually lands in the directory-mapped file.
        store = FlatFileSessionStore(sessions_dir=self.test_dir)
        self.assertIsInstance(store, SqliteSessionStore)

        store.append_message("session1", "user", "hello")

        plain = SqliteSessionStore(db_path=os.path.join(self.test_dir, "memory.db"))
        history = plain.load_history("session1")
        self.assertEqual(len(history), 1, "message written via the shim was not found in <sessions_dir>/memory.db")
        self.assertEqual(history[0]["from"], "user")
        self.assertEqual(history[0]["message"], "hello")

if __name__ == "__main__":
    unittest.main()
