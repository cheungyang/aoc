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
        self.assertEqual(
            set(pre.REGISTRY),
            {
                "always", "files_changed_since", "files_exist", "file_matches",
                "json_non_empty", "task_list_non_empty", "project_list_non_empty",
                "sleep_logged_today", "market_screen", "property_screen",
                "credit_windows", "ha_audit", "morning_briefing", "projects_attention",
            },
        )


if __name__ == "__main__":
    unittest.main()
