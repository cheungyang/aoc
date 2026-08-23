import unittest
import tempfile
import os
import json
from graphs.coding.schemas import TaskEnvelope
from graphs.coding.utils.dag import (
    get_completed_task_ids,
    get_runnable_tasks,
    update_task_in_queue,
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
        queue = update_task_in_queue(queue, "TASK-A", status="completed")
        runnable = get_runnable_tasks(queue)
        self.assertEqual(len(runnable), 1)
        self.assertEqual(runnable[0]["task_id"], "TASK-B")

        # Complete TASK-B
        queue = update_task_in_queue(queue, "TASK-B", status="completed")
        runnable = get_runnable_tasks(queue)
        self.assertEqual(len(runnable), 1)
        self.assertEqual(runnable[0]["task_id"], "TASK-C")

        # Complete TASK-C
        queue = update_task_in_queue(queue, "TASK-C", status="completed")
        runnable = get_runnable_tasks(queue)
        self.assertEqual(len(runnable), 0)

    def test_update_task_in_queue_v2_fields(self):
        task: TaskEnvelope = {
            "task_id": "TASK-01",
            "status": "pending",
            "dependencies": []
        }
        queue = [task]
        
        # 1. Update to in_review
        queue = update_task_in_queue(
            queue,
            "TASK-01",
            status="in_review",
            run_id="run_123",
            branch_name="feat/test/auth",
            pr_url="https://github.com/org/repo/pull/1"
        )
        self.assertEqual(queue[0]["status"], "in_review")
        self.assertEqual(queue[0]["pr_url"], "https://github.com/org/repo/pull/1")

        # 2. Update to completed with commit_url
        queue = update_task_in_queue(
            queue,
            "TASK-01",
            status="completed",
            commit_url="https://github.com/org/repo/commit/sha123"
        )
        self.assertEqual(queue[0]["status"], "completed")
        self.assertEqual(queue[0]["commit_url"], "https://github.com/org/repo/commit/sha123")

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

if __name__ == "__main__":
    unittest.main()
