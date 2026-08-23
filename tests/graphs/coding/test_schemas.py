import unittest
from graphs.coding.schemas import TaskEnvelope, CodingState, TaskStatus

class TestCodingSchemas(unittest.TestCase):
    def test_task_envelope_structure(self):
        task: TaskEnvelope = {
            "task_id": "TASK-01",
            "project_name": "egm_coding_graph",
            "feature_name": "window_id",
            "spec_path": "wiki/software/specs/feature.md",
            "dependencies": ["TASK-00"],
            "allowed_files": ["src/feature.py", "tests/test_feature.py"],
            "verification_command": "pytest tests/test_feature.py",
            "acceptance_criteria": "Given X When Y Then Z",
            "status": "in_review",
            "run_id": "run_8F2A",
            "branch_name": "feat/egm/TASK-01_run_8F2A",
            "pr_url": "https://github.com/org/repo/pull/1",
            "commit_url": "https://github.com/org/repo/commit/sha123",
            "error_message": None
        }
        self.assertEqual(task["task_id"], "TASK-01")
        self.assertEqual(task["status"], "in_review")
        self.assertEqual(task["commit_url"], "https://github.com/org/repo/commit/sha123")
        self.assertEqual(len(task["dependencies"]), 1)
        self.assertEqual(len(task["allowed_files"]), 2)

    def test_coding_state_structure(self):
        state: CodingState = {
            "build_request_path": "pkm/wiki/software/build_request.json",
            "project_name": "egm_coding_graph",
            "max_concurrency": 1,
            "queue": [],
            "run_id": "run_8F2A",
            "attempt_count": 0,
            "test_run_passed": True,
            "critic_passed": True,
            "pr_url": "https://github.com/org/repo/pull/1",
            "pr_number": 1,
            "commit_url": "https://github.com/org/repo/commit/sha123",
            "github_pr_comments": ["Great work, please add comment"],
            "hitl_decision": "approved"
        }
        self.assertEqual(state["run_id"], "run_8F2A")
        self.assertTrue(state["test_run_passed"])
        self.assertEqual(state["hitl_decision"], "approved")
        self.assertEqual(state["pr_number"], 1)
        self.assertEqual(state["commit_url"], "https://github.com/org/repo/commit/sha123")
        self.assertEqual(len(state["github_pr_comments"]), 1)

if __name__ == "__main__":
    unittest.main()
