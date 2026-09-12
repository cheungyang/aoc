"""Enforces one unit-test file per production module in the coding pipeline.

This is a structural rule, not a behavioural one. It exists because the
alternative decays quietly: a module gets folded into a neighbour's test file,
that file is later split or deleted, and the module ends up with no direct
coverage while the suite stays green. That is exactly what happened here once --
deleting the v1 provisioner's test suite silently took `utils/preflight.py`'s
only tests with it, and nothing noticed because the scheduler tests mocked
`preflight_tick` out.

Scope is deliberately limited to the coding graph and the scripts that drive it.
This is not a repo-wide mandate.

Adding a module here means adding its test file. If a module genuinely cannot be
tested in isolation, add it to EXEMPT with a reason -- an explicit, reviewed
exemption is fine; an invisible gap is not.
"""
import os
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# (directory, recursive) pairs whose modules must each have a test file.
COVERED_TREES = [
    ("graphs/coding", True),
]

# Individual modules outside those trees.
COVERED_MODULES = [
    "scripts/_bootstrap.py",
    "scripts/coding_admin.py",
    "scripts/coding_bot_setup.py",
    "scripts/coding_tick.py",
]

# Test files live flat under here regardless of the module's subpackage.
TEST_DIRS = ["tests/graphs/coding", "tests/scripts", "tests/core/util"]

EXEMPT = {
    # Package markers hold no logic.
    "__init__.py",
}


def _expected_test_names(module_path):
    """`utils/repo.py` -> `test_repo.py`; `_bootstrap.py` -> `test_bootstrap.py`."""
    stem = os.path.basename(module_path)[:-len(".py")]
    return {f"test_{stem}.py", f"test_{stem.lstrip('_')}.py"}


def _iter_modules():
    for tree, recursive in COVERED_TREES:
        abs_tree = os.path.join(PROJECT_ROOT, tree)
        for root, dirs, files in os.walk(abs_tree):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in sorted(files):
                if name.endswith(".py") and name not in EXEMPT:
                    yield os.path.relpath(os.path.join(root, name), PROJECT_ROOT)
            if not recursive:
                dirs[:] = []
    for module in COVERED_MODULES:
        if os.path.basename(module) not in EXEMPT:
            yield module


def _existing_test_files():
    found = set()
    for test_dir in TEST_DIRS:
        abs_dir = os.path.join(PROJECT_ROOT, test_dir)
        if not os.path.isdir(abs_dir):
            continue
        found.update(
            name for name in os.listdir(abs_dir)
            if name.startswith("test_") and name.endswith(".py")
        )
    return found


class TestEveryModuleHasATestFile(unittest.TestCase):
    def test_no_coding_module_is_missing_its_test_file(self):
        available = _existing_test_files()

        missing = [
            module for module in _iter_modules()
            if not (_expected_test_names(module) & available)
        ]

        self.assertEqual(
            missing, [],
            "These modules have no correspondingly-named test file. Create "
            "`test_<module>.py` in the matching tests/ directory, or add the "
            "module to EXEMPT with a reason:\n  " + "\n  ".join(missing)
        )

    def test_the_rule_covers_something(self):
        """A path typo in COVERED_TREES would make the rule above vacuously
        true, which is worse than not having it."""
        self.assertGreater(len(list(_iter_modules())), 15)


if __name__ == "__main__":
    unittest.main()
