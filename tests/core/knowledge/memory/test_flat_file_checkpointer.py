import unittest
import os
import shutil
import tempfile
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from core.knowledge.memory.flat_file_checkpointer import FlatFileCheckpointer
from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer

class TestFlatFileCheckpointer(unittest.TestCase):
    """
    FlatFileCheckpointer is a backwards-compatibility shim (core/knowledge/memory/flat_file_checkpointer.py):
    a constructor-only subclass of SqliteCheckpointer whose *only* own behaviour is mapping the legacy
    `directory=` argument onto <directory>/memory.db.

    Every read/write/list/archive behaviour is inherited verbatim and is already covered by
    TestSqliteCheckpointer in test_sqlite_checkpointer.py, so this file deliberately tests only the
    shim's own contract instead of re-running the parent class's test suite through the subclass.
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_directory_argument_maps_to_memory_db_inside_directory(self):
        # The whole reason this shim exists: legacy callers passed a directory, not a db file.
        checkpointer = FlatFileCheckpointer(directory=self.test_dir)
        expected_db = os.path.join(self.test_dir, "memory.db")
        self.assertEqual(checkpointer.db_path, expected_db)
        # SqliteCheckpointer.__init__ opens the db, so the mapped file must exist on disk.
        self.assertTrue(
            os.path.exists(expected_db),
            f"expected the shim to create {expected_db}, dir contains {os.listdir(self.test_dir)}"
        )

    def test_db_path_argument_takes_precedence_over_directory(self):
        explicit_db = os.path.join(self.test_dir, "explicit.db")
        checkpointer = FlatFileCheckpointer(directory=self.test_dir, db_path=explicit_db)
        self.assertEqual(checkpointer.db_path, explicit_db)
        self.assertTrue(os.path.exists(explicit_db))
        # The directory mapping must not also fire when an explicit db_path was given.
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, "memory.db")))

    def test_is_a_working_sqlite_checkpointer_subclass(self):
        # Inherited behaviour is tested next door; here we only prove the subclass wiring is real
        # and that inherited I/O actually lands in the directory-mapped file.
        checkpointer = FlatFileCheckpointer(directory=self.test_dir)
        self.assertIsInstance(checkpointer, SqliteCheckpointer)

        config = {"configurable": {"thread_id": "shim_thread"}}
        checkpointer.put(config, {"id": "cp1"}, {"step": 1}, {})

        plain = SqliteCheckpointer(db_path=os.path.join(self.test_dir, "memory.db"))
        cp_tuple = plain.get_tuple(config)
        self.assertIsNotNone(cp_tuple, "checkpoint written via the shim was not found in <directory>/memory.db")
        self.assertEqual(cp_tuple.checkpoint["id"], "cp1")

if __name__ == "__main__":
    unittest.main()
