"""Tests for the action -> backend routing table.

Restating the table here ("get_state maps to REST") would only assert that a
dict literal is itself, and would need editing every time a row is added. So
these test the table's *invariants* instead -- the properties other modules
silently rely on, which is where a real mistake would land:

  * read and write vocabularies stay disjoint (the guard layer branches on it),
  * every backend is a real backend (catches a typo'd value),
  * the table and the tool's IMPLEMENTED set do not drift apart.
"""
import unittest

from core.integrations.homeassistant import routing


class TestVocabulary(unittest.TestCase):
    def test_read_and_write_are_disjoint(self):
        """An action in both sets would bypass the guards entirely.

        tools/home_assistant.py branches on `action in WRITE_ACTIONS` before it
        checks anything else. An action that appeared in both tables would still
        be caught by that branch today, but the merged BACKENDS dict would
        silently take the write entry, so the two would disagree about the
        transport. Keeping them disjoint is what makes the branch total.
        """
        overlap = routing.READ_ACTIONS & routing.WRITE_ACTIONS
        self.assertEqual(overlap, frozenset(), f"actions declared both read and write: {sorted(overlap)}")

    def test_merging_loses_nothing(self):
        """BACKENDS is a dict merge, so a duplicate key would silently vanish."""
        self.assertEqual(
            len(routing.BACKENDS),
            len(routing.READ_BACKENDS) + len(routing.WRITE_BACKENDS),
        )
        self.assertEqual(routing.ALL_ACTIONS, routing.READ_ACTIONS | routing.WRITE_ACTIONS)

    def test_every_backend_is_a_known_transport(self):
        """Catches a typo'd value, which would otherwise fail far from its cause."""
        known = {routing.REST, routing.WS, routing.MCP}
        for action, backend in routing.BACKENDS.items():
            self.assertIn(backend, known, f"{action} routes to unknown backend {backend!r}")


class TestLookup(unittest.TestCase):
    def test_backend_for_agrees_with_the_table(self):
        for action, backend in routing.BACKENDS.items():
            self.assertEqual(routing.backend_for(action), backend, action)

    def test_unknown_action_raises_rather_than_defaulting(self):
        """A typo must fail loudly here, not 404 later against the wrong plane."""
        with self.assertRaises(KeyError):
            routing.backend_for("no_such_action")

    def test_is_write_agrees_with_the_table(self):
        for action in routing.WRITE_ACTIONS:
            self.assertTrue(routing.is_write(action), action)
        for action in routing.READ_ACTIONS:
            self.assertFalse(routing.is_write(action), action)
        self.assertFalse(routing.is_write("no_such_action"))


class TestToolAgreesWithTable(unittest.TestCase):
    """The table and the tool are edited separately and must not drift.

    These are the tests most likely to earn their keep: adding a row to routing
    without a dispatch branch, or a branch without a row, is the natural mistake
    to make when extending this integration.
    """

    def setUp(self):
        from tools.home_assistant import IMPLEMENTED, WRITE_IMPLEMENTED

        self.implemented = IMPLEMENTED
        self.write_implemented = WRITE_IMPLEMENTED

    def test_every_implemented_action_is_routed(self):
        unrouted = self.implemented - routing.ALL_ACTIONS
        self.assertEqual(unrouted, frozenset(), f"implemented but not in the routing table: {sorted(unrouted)}")

    def test_no_implemented_action_is_a_write(self):
        """IMPLEMENTED is the read dispatch table; a write here would skip the guards."""
        writes = self.implemented & routing.WRITE_ACTIONS
        self.assertEqual(writes, frozenset(), f"mutating actions in IMPLEMENTED: {sorted(writes)}")

    def test_implemented_is_exactly_the_declared_reads(self):
        """Pins the read boundary: every routed read is dispatched, and nothing
        is dispatched that the table does not declare.

        This used to exclude MCP-backed reads, which were routed but not yet
        served. Phase 6 closed that gap, so the carve-out is gone and the two
        sets must now match exactly. A REST-backed read added to the table but
        never dispatched fails here -- otherwise the tool reports it as an
        unknown action while the table claims it exists.
        """
        self.assertEqual(set(self.implemented), set(routing.READ_ACTIONS))

    def test_every_dispatched_write_is_a_declared_write(self):
        """A write that is dispatched but not in WRITE_ACTIONS would never be guarded.

        The tool only routes an action through `_guard_write` when it is in
        `routing.WRITE_ACTIONS`. Something dispatched as a write but absent from
        that set would be treated as a read: no confirmation, no deny-list, no
        kill switch. This is the most safety-critical assertion in the file.
        """
        undeclared = self.write_implemented - routing.WRITE_ACTIONS
        self.assertEqual(
            undeclared, frozenset(),
            f"dispatched as writes but not declared as writes: {sorted(undeclared)}",
        )

    def test_the_two_dispatch_tables_do_not_overlap(self):
        overlap = self.implemented & self.write_implemented
        self.assertEqual(overlap, frozenset(), f"in both dispatch tables: {sorted(overlap)}")


if __name__ == "__main__":
    unittest.main()
