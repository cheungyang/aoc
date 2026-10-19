import unittest

from core.scheduler import preconditions as pre
from core.scheduler.preconditions import PreconditionError, build_precondition
from tests.core.scheduler.preconditions import ctx


class TestBuild(unittest.TestCase):
    def test_unknown_type(self):
        with self.assertRaises(PreconditionError) as cm:
            build_precondition({"type": "nope"})
        self.assertIn("unknown precondition type", str(cm.exception))

    def test_missing_type_and_non_dict(self):
        with self.assertRaises(PreconditionError):
            build_precondition({})
        with self.assertRaises(PreconditionError):
            build_precondition("always")

    def test_always_requires_reason(self):
        with self.assertRaises(PreconditionError):
            build_precondition({"type": "always"})
        with self.assertRaises(PreconditionError):
            build_precondition({"type": "always", "reason": "  "})
        decision = build_precondition({"type": "always", "reason": "external"}).evaluate(ctx())
        self.assertTrue(decision)
        self.assertIn("external", decision.reason)

    def test_unknown_params_rejected(self):
        with self.assertRaises(PreconditionError) as cm:
            build_precondition({"type": "files_exist", "paths": ["x"], "path": "y"})
        self.assertIn("does not take", str(cm.exception))

    def test_required_params(self):
        for bad in (
            {"type": "files_exist"},
            {"type": "files_changed_since", "paths": []},
            {"type": "file_matches", "path": "x"},
            {"type": "file_matches", "path": "x", "pattern": "("},
            {"type": "json_non_empty", "path": "x"},
            {"type": "task_list_non_empty", "filter": "someday"},
        ):
            with self.subTest(bad=bad), self.assertRaises(PreconditionError):
                build_precondition(bad)

    def test_registry_is_complete(self):
        """The core types; scans belong to their agents' own code."""
        self.assertEqual(
            set(pre.REGISTRY),
            {
                "always", "files_changed_since", "files_exist", "file_matches",
                "json_non_empty", "task_list_non_empty", "project_list_non_empty",
                "sleep_logged_today",
            },
        )

    def test_a_scan_resolves_only_for_its_own_agent(self):
        scans = {agent: pre.agent_preconditions(agent) for agent in ("market-scout", "day-planner")}
        self.assertIn("market_screen", scans["market-scout"])
        self.assertIsInstance(build_precondition({"type": "market_screen"}, agent_id="market-scout"),
                              scans["market-scout"]["market_screen"])
        with self.assertRaises(PreconditionError) as cm:
            build_precondition({"type": "market_screen"}, agent_id="day-planner")
        self.assertIn("unknown precondition type 'market_screen'", str(cm.exception))
        with self.assertRaises(PreconditionError):
            build_precondition({"type": "market_screen"})


if __name__ == "__main__":
    unittest.main()
