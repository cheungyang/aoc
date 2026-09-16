import unittest
import tempfile
import os
import json
from graphs.coding.schemas import TaskEnvelope
from graphs.coding.utils.dag import (
    get_completed_task_ids,
    get_runnable_tasks,
    load_manifest,
    save_manifest
)

class TestDAGHelpers(unittest.TestCase):
    def test_dependency_resolution(self):
        task_a: TaskEnvelope = {
            "task_id": "TASK-A",
            "project_name": "test_proj",
            "feature_name": "db",
            "spec_path": "specs/db.md",
            "dependencies": [],
            "allowed_files": ["db.py"],
            "verification_command": "pytest",
            "acceptance_criteria": "db works",
            "status": "pending"
        }
        task_b: TaskEnvelope = {
            "task_id": "TASK-B",
            "project_name": "test_proj",
            "feature_name": "api",
            "spec_path": "specs/api.md",
            "dependencies": ["TASK-A"],
            "allowed_files": ["api.py"],
            "verification_command": "pytest",
            "acceptance_criteria": "api works",
            "status": "pending"
        }
        task_c: TaskEnvelope = {
            "task_id": "TASK-C",
            "project_name": "test_proj",
            "feature_name": "ui",
            "spec_path": "specs/ui.md",
            "dependencies": ["TASK-B"],
            "allowed_files": ["ui.py"],
            "verification_command": "pytest",
            "acceptance_criteria": "ui works",
            "status": "pending"
        }

        queue = [task_a, task_b, task_c]

        # Initially, only TASK-A is runnable because TASK-B depends on TASK-A
        runnable = get_runnable_tasks(queue)
        self.assertEqual(len(runnable), 1)
        self.assertEqual(runnable[0]["task_id"], "TASK-A")

        # Complete TASK-A
        task_a["status"] = "completed"
        runnable = get_runnable_tasks(queue)
        self.assertEqual(len(runnable), 1)
        self.assertEqual(runnable[0]["task_id"], "TASK-B")

        # Complete TASK-B
        task_b["status"] = "completed"
        runnable = get_runnable_tasks(queue)
        self.assertEqual(len(runnable), 1)
        self.assertEqual(runnable[0]["task_id"], "TASK-C")

        # Complete TASK-C
        task_c["status"] = "completed"
        runnable = get_runnable_tasks(queue)
        self.assertEqual(len(runnable), 0)

    def test_manifest_load_and_save(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            path = tf.name

        try:
            data = {
                "version": "2.0",
                "project_name": "test_manifest",
                "max_concurrency": 2,
                "queue": [
                    {
                        "task_id": "TASK-01",
                        "status": "pending",
                        "dependencies": []
                    }
                ]
            }
            save_manifest(path, data)
            loaded = load_manifest(path)
            self.assertEqual(loaded["version"], "2.0")
            self.assertEqual(loaded["project_name"], "test_manifest")
            self.assertEqual(len(loaded["queue"]), 1)
            self.assertEqual(loaded["queue"][0]["task_id"], "TASK-01")
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_resolve_path_success(self):
        from graphs.coding.utils.dag import resolve_path
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf:
            path = tf.name
            tf.write(b"# Spec")

        try:
            resolved = resolve_path(path, must_exist=True)
            self.assertEqual(resolved, os.path.abspath(path))
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_resolve_path_file_not_found(self):
        from graphs.coding.utils.dag import resolve_path
        with self.assertRaises(FileNotFoundError):
            resolve_path("non_existent_file_12345.md", must_exist=True)

    def test_resolve_path_empty_must_exist(self):
        from graphs.coding.utils.dag import resolve_path
        with self.assertRaises(ValueError):
            resolve_path("", must_exist=True)

    def test_resolve_path_default_fallback(self):
        from graphs.coding.utils.dag import resolve_path
        res = resolve_path(None, default="foo/bar.json")
        self.assertTrue(res.endswith("foo/bar.json"))

class TestOneManifestPerProject(unittest.TestCase):
    """A shared queue made every project's tasks each other's problem: one
    halted task held the only concurrency slot, and `repo` and `setup_command`
    had to be true of everything in it at once."""

    def test_a_project_name_becomes_one_folder_name(self):
        from graphs.coding.utils.dag import project_slug

        for given in ("French Learning Cards", "french_learning_cards",
                      "  French-Learning-Cards  "):
            self.assertEqual(project_slug(given), "french-learning-cards", given)

    def test_the_manifest_lives_beside_the_projects_specs(self):
        from graphs.coding.utils.dag import manifest_path_for_project

        path = manifest_path_for_project("French Learning Cards")

        self.assertTrue(
            path.endswith("pkm/wiki/software/french-learning-cards/build_request.json"),
            path
        )

    def test_a_nameless_project_resolves_to_nothing(self):
        """Better an empty string the caller must handle than a path that
        happens to point at some other project's queue."""
        from graphs.coding.utils.dag import manifest_path_for_project

        self.assertEqual(manifest_path_for_project("  "), "")

    def test_discovery_finds_one_manifest_per_project(self):
        from graphs.coding.utils.dag import discover_manifests

        with tempfile.TemporaryDirectory() as tmp:
            for name in ("beta", "alpha"):
                os.makedirs(os.path.join(tmp, name))
                with open(os.path.join(tmp, name, "build_request.json"), "w") as f:
                    json.dump({"queue": []}, f)
            # A stray file at the root is not a project and must not be picked up.
            with open(os.path.join(tmp, "build_request.json"), "w") as f:
                json.dump({"queue": []}, f)

            found = discover_manifests(tmp)

        self.assertEqual(len(found), 2)
        self.assertTrue(found[0].endswith("alpha/build_request.json"), found)
        self.assertTrue(found[1].endswith("beta/build_request.json"), found)

    def test_discovery_of_an_empty_tree_is_empty_not_an_error(self):
        from graphs.coding.utils.dag import discover_manifests

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(discover_manifests(tmp), [])

    def test_resolving_without_a_manifest_refuses_rather_than_guesses(self):
        """The old global default is how a run that was never told which project
        it was for still found a queue — and started on somebody else's tasks."""
        from graphs.coding.utils.dag import resolve_manifest_path

        with self.assertRaises(ValueError) as caught:
            resolve_manifest_path(None)

        self.assertIn("one project at a time", str(caught.exception))

    def test_an_explicit_manifest_still_resolves(self):
        from graphs.coding.utils.dag import resolve_manifest_path

        res = resolve_manifest_path("pkm/wiki/software/demo/build_request.json")

        self.assertTrue(res.endswith("pkm/wiki/software/demo/build_request.json"))
        self.assertTrue(os.path.isabs(res))


if __name__ == "__main__":
    unittest.main()
