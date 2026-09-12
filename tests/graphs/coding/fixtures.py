"""Shared scaffolding for the tick node tests.

Every node in the tick reads and writes the same manifest, so a fixture shared
between test cases would make them pass or fail depending on the order they ran
in. Each case gets its own throwaway manifest and worktree instead.
"""
import json
import os
import tempfile
import unittest

from graphs.coding.utils import manifest as manifest_store


def _task(**overrides):
    task = {
        "task_id": "T1",
        "status": "pending",
        "stage": "queued",
        "dependencies": [],
        "verification_command": "pytest -q",
        "attempts": {},
    }
    task.update(overrides)
    return task


class ManifestFixture(unittest.IsolatedAsyncioTestCase):
    """Every node writes through the manifest, so each test gets its own."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manifest_path = os.path.join(self.tmp.name, "build_request.json")
        self.workspace = os.path.join(self.tmp.name, "ws")
        os.makedirs(self.workspace, exist_ok=True)

    def write_manifest(self, tasks, **manifest_fields):
        manifest = {"version": "3.0", "project_name": "demo", "queue": tasks}
        manifest.update(manifest_fields)
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f)

    def stored(self, task_id="T1"):
        return manifest_store.find_task(
            manifest_store.load_manifest(self.manifest_path), task_id
        )

    def base_state(self, task, **overrides):
        state = {
            "build_request_path": self.manifest_path,
            "current_task": task,
            "workspace_path": self.workspace,
            "branch_name": "feat/demo/t1",
            "project_name": "demo",
            "tick_report": [],
            "repo": {"slug": "org/repo", "default_branch": "main"},
        }
        state.update(overrides)
        return state
