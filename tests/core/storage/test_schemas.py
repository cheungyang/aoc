"""The core/storage layout: every schema file belongs to one store, and SQLite is opened only there."""
import importlib
import inspect
import pkgutil
import re
import sqlite3
import subprocess
import unittest
from pathlib import Path

import core.storage
from core.storage import Store, apply_schema
from core.storage.base import SCHEMAS_DIR, load_schema

REPO = Path(__file__).resolve().parents[3]
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def stores():
    """Every Store subclass defined in a core/storage module."""
    found = []
    for module in pkgutil.iter_modules(core.storage.__path__):
        mod = importlib.import_module(f"core.storage.{module.name}")
        found += [cls for _, cls in inspect.getmembers(mod, inspect.isclass)
                  if issubclass(cls, Store) and cls is not Store and cls.__module__ == mod.__name__]
    return found


class TestSchemaRegistry(unittest.TestCase):

    def test_every_schema_file_belongs_to_exactly_one_store(self):
        owners = {}
        for cls in stores():
            for name in cls.schema + cls.optional_schema:
                owners.setdefault(name, []).append(cls.__name__)
        files = sorted(p.stem for p in SCHEMAS_DIR.glob("*.sql"))
        self.assertEqual(sorted(owners), files, "a store names a missing file, or a file has no store")
        for name, classes in owners.items():
            with self.subTest(schema=name):
                self.assertEqual(len(classes), 1, f"{name}.sql is claimed by {classes}")

    def test_every_store_module_holds_a_store(self):
        modules = {cls.__module__.rsplit(".", 1)[1] for cls in stores()}
        own = {m.name for m in pkgutil.iter_modules(core.storage.__path__)} - {"base", "util"}
        self.assertEqual(modules, own)

    def test_every_schema_applies_twice(self):
        """Schema files are idempotent: stores apply them on every connection."""
        for path in sorted(SCHEMAS_DIR.glob("*.sql")):
            with self.subTest(schema=path.stem):
                identifiers = {key: "ctx_test" for key in PLACEHOLDER.findall(load_schema(path.stem))}
                conn = sqlite3.connect(":memory:")
                try:
                    for _ in range(2):
                        self._apply(conn, path.stem, identifiers)
                finally:
                    conn.close()

    def _apply(self, conn, name, identifiers):
        try:
            apply_schema(conn, name, **identifiers)
        except sqlite3.OperationalError as e:
            if "no such table" in str(e):  # an optional part (FTS) on top of its base schema
                base = name.rsplit("_", 1)[0]
                apply_schema(conn, base, **identifiers)
                apply_schema(conn, name, **identifiers)
            elif "fts5" in str(e):
                self.skipTest("this SQLite has no FTS5")
            else:
                raise

    def test_every_schema_file_says_who_owns_it(self):
        for path in sorted(SCHEMAS_DIR.glob("*.sql")):
            with self.subTest(schema=path.stem):
                self.assertTrue(path.read_text(encoding="utf-8").startswith("--"), "a header comment comes first")


class TestStorageBoundary(unittest.TestCase):
    """SQLite files are opened only by core/storage; everything else goes through a store."""

    def test_nothing_outside_core_storage_connects(self):
        tracked = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"],
                                 cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
        offenders = []
        for rel in tracked:
            if rel.startswith(("core/storage/", "tests/")) or not (REPO / rel).is_file():
                continue
            text = (REPO / rel).read_text(encoding="utf-8")
            if re.search(r"\bsqlite3\.connect\(", text):
                offenders.append(rel)
        self.assertEqual(offenders, [], "open the file through a core/storage store instead")


if __name__ == "__main__":
    unittest.main()
