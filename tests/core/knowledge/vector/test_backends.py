"""Tests for backend selection.

Selection has to be right on a machine where the wrong answer is fatal, so these
cover the auto path with the probe faked in both directions rather than only the
explicit overrides.
"""

import unittest
from unittest.mock import patch

from core.util.config import Config
from core.knowledge.vector import backends, probe


class TestResolveBackendName(unittest.TestCase):

    def setUp(self):
        Config().reset()
        backends.reset()
        probe.reset_cache()

    def tearDown(self):
        Config().reset()
        backends.reset()
        probe.reset_cache()

    def test_explicit_lancedb(self):
        self.assertEqual(backends.resolve_backend_name("lancedb"), backends.LANCEDB)

    def test_explicit_numpy(self):
        self.assertEqual(backends.resolve_backend_name("numpy"), backends.NUMPY)

    def test_explicit_choice_skips_the_probe(self):
        # The probe costs about a second; an explicit choice should not pay it.
        with patch.object(probe, "lancedb_usable") as mock_probe:
            backends.resolve_backend_name("numpy")
            backends.resolve_backend_name("lancedb")
        mock_probe.assert_not_called()

    def test_case_and_whitespace_insensitive(self):
        self.assertEqual(backends.resolve_backend_name("  LanceDB "), backends.LANCEDB)
        self.assertEqual(backends.resolve_backend_name("NumPy"), backends.NUMPY)

    def test_auto_prefers_lancedb_when_the_probe_passes(self):
        with patch.object(probe, "lancedb_usable", return_value=True):
            self.assertEqual(backends.resolve_backend_name("auto"), backends.LANCEDB)

    def test_auto_falls_back_to_numpy_when_the_probe_fails(self):
        with patch.object(probe, "lancedb_usable", return_value=False):
            self.assertEqual(backends.resolve_backend_name("auto"), backends.NUMPY)

    def test_unknown_value_is_treated_as_auto(self):
        with patch.object(probe, "lancedb_usable", return_value=False):
            self.assertEqual(backends.resolve_backend_name("postgres"), backends.NUMPY)

    def test_empty_value_is_treated_as_auto(self):
        with patch.object(probe, "lancedb_usable", return_value=False):
            self.assertEqual(backends.resolve_backend_name(""), backends.NUMPY)

    def test_reads_config_when_not_given_a_value(self):
        Config().knowledge_backend = "numpy"
        self.assertEqual(backends.resolve_backend_name(), backends.NUMPY)


class TestGetBackend(unittest.TestCase):

    def setUp(self):
        Config().reset()
        backends.reset()

    def tearDown(self):
        Config().reset()
        backends.reset()

    def test_returns_the_numpy_module(self):
        Config().knowledge_backend = "numpy"
        self.assertEqual(backends.get_backend().name, "numpy")

    def test_selection_is_memoized(self):
        Config().knowledge_backend = "numpy"
        with patch.object(backends, "resolve_backend_name",
                          return_value=backends.NUMPY) as mock_resolve:
            backends.get_backend()
            backends.get_backend()
        self.assertEqual(mock_resolve.call_count, 1)

    def test_reset_clears_the_memo(self):
        Config().knowledge_backend = "numpy"
        first = backends.get_backend()

        backends.reset()
        Config().knowledge_backend = "lancedb"
        with patch.object(probe, "lancedb_usable", return_value=True):
            second = backends.get_backend()

        self.assertEqual(first.name, "numpy")
        self.assertEqual(second.name, "lancedb")

    def test_explicit_argument_does_not_poison_the_memo(self):
        Config().knowledge_backend = "numpy"
        one_off = backends.get_backend("lancedb")
        self.assertEqual(one_off.name, "lancedb")
        # The next default call must still honour the configured value.
        self.assertEqual(backends.get_backend().name, "numpy")

    def test_force_reselect(self):
        Config().knowledge_backend = "numpy"
        backends.get_backend()
        Config().knowledge_backend = "lancedb"
        self.assertEqual(backends.get_backend(force_reselect=True).name, "lancedb")


class TestFacadeStaysImportSafe(unittest.TestCase):
    """The facade must be importable on a machine that cannot load lancedb.

    This is the regression that started the whole exercise: a module-scope
    `import lancedb` in db.py took down any process that touched the knowledge
    stack, including the tool loader merely discovering vault_search.
    """

    def test_db_module_does_not_import_lancedb(self):
        import ast
        import inspect
        from core.knowledge.vector import db

        tree = ast.parse(inspect.getsource(db))
        module_level_imports = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                module_level_imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_level_imports.add(node.module.split(".")[0])

        self.assertNotIn("lancedb", module_level_imports)
        self.assertNotIn("pyarrow", module_level_imports)

    def test_importing_the_facade_does_not_select_a_backend(self):
        # Selection triggers the probe and, on the lancedb path, a native import.
        # It must be deferred until something actually opens a store.
        backends.reset()
        import importlib
        from core.knowledge.vector import db

        importlib.reload(db)
        self.assertIsNone(backends._selected)


if __name__ == "__main__":
    unittest.main()
