import unittest
from graphs.coding.adapters import prepare_input, format_hitl_presentation, format_output
from langchain_core.messages import AIMessage

class TestCodingAdapters(unittest.TestCase):
    def test_prepare_input_defaults(self):
        res = prepare_input(
            query="Run task",
            project_path="/path/to/project",
            thread_id="thread_123"
        )
        self.assertEqual(res["project_path"], "/path/to/project")
        self.assertEqual(res["thread_id"], "thread_123")
        self.assertEqual(res["max_concurrency"], 1)
        self.assertEqual(res["max_retries"], 3)
        self.assertEqual(res["error_message"], "")

    def test_prepare_input_missing_project_path_error(self):
        res = prepare_input(query="Run build without dir")
        self.assertIn("Initialization error: 'project_path' is required", res["error_message"])

    def test_prepare_input_project_path_resolution(self):
        res = prepare_input(
            query="Run build",
            project_path="pkm/wiki/software/aoc"
        )
        self.assertTrue(res["project_path"].endswith("pkm/wiki/software/aoc"))
        self.assertTrue(res["build_request_path"].endswith("pkm/wiki/software/build_request.json"))

    def test_prepare_input_human_feedback_extraction(self):
        res = prepare_input(
            query="Approve, please merge!",
            project_path="/repo"
        )
        self.assertEqual(res["latest_human_feedback"], "Approve, please merge!")

    def test_format_hitl_presentation_v2(self):
        state = {
            "current_task": {"task_id": "AOC-01"},
            "run_id": "run_A1B2",
            "branch_name": "feat/aoc/test_run_A1B2",
            "pr_url": "https://github.com/org/repo/pull/99",
            "test_run_passed": True,
            "critic_passed": True
        }
        out = format_hitl_presentation(state)
        self.assertIn("### 🔍 Coding Graph HITL Review Gate", out)
        self.assertIn("AOC-01", out)
        self.assertIn("https://github.com/org/repo/pull/99", out)
        self.assertIn("ALL TESTS PASSING", out)
        self.assertIn("APPROVED", out)

    def test_format_output_pending_review(self):
        state = {
            "hitl_decision": "pending_review",
            "current_task": {"task_id": "AOC-01"},
            "pr_url": "https://github.com/org/repo/pull/99",
            "test_run_passed": True,
            "critic_passed": True
        }
        out = format_output(state)
        self.assertIn("### 🔍 Coding Graph HITL Review Gate", out)

    def test_format_output_completed_with_commit_url(self):
        state = {
            "completed_tasks": ["AOC-01"],
            "pr_url": "https://github.com/org/repo/pull/99",
            "commit_url": "https://github.com/org/repo/commit/sha123"
        }
        out = format_output(state)
        self.assertIn("Coding Execution Completed & Merged!", out)
        self.assertIn("https://github.com/org/repo/commit/sha123", out)
        self.assertIn("https://github.com/org/repo/pull/99", out)

    def test_format_output_error_message(self):
        state = {"error_message": "Disk full"}
        out = format_output(state)
        self.assertIn("Coding graph execution error: Disk full", out)

if __name__ == '__main__':
    unittest.main()
