import unittest
from unittest.mock import patch

from graphs.coding.adapters import (
    prepare_input,
    format_output,
    format_tick_report,
)


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
        self.assertEqual(res["error_message"], "")

    def test_prepare_input_without_project_path_is_valid(self):
        """A scheduled tick has no project in mind; the manifest carries spec paths."""
        res = prepare_input(query="Run build without dir")
        self.assertEqual(res["error_message"], "")
        self.assertEqual(res["project_path"], "")
        self.assertTrue(res["build_request_path"].endswith("pkm/wiki/software/build_request.json"))

    def test_prepare_input_seeds_v2_fields(self):
        res = prepare_input(query="tick")
        self.assertEqual(res["tick_report"], [])
        self.assertEqual(res["tick_handled"], [])
        self.assertEqual(res["graph_id"], "coding")
        self.assertNotIn("audit_mode", res)
        self.assertIsInstance(res["repo"], dict)
        self.assertIsInstance(res["reviewers"], list)

    def test_required_tools_are_the_graphs_own_grant(self):
        """The grant *is* the requirement — there is no second list to keep in sync."""
        with patch("graphs.coding.adapters._load_graph_config", return_value={
            "graph_id": "coding",
            "tools": {"bash": {}, "filesystem": {}},
        }):
            res = prepare_input(query="tick")

        self.assertEqual(res["required_tools"], ["bash", "filesystem"])

    def test_a_graph_that_grants_nothing_requires_nothing(self):
        with patch("graphs.coding.adapters._load_graph_config", return_value={}):
            res = prepare_input(query="tick")

        self.assertEqual(res["required_tools"], [])

    def test_the_real_graph_config_grants_the_worker_its_tools(self):
        res = prepare_input(query="tick")

        self.assertIn("bash", res["required_tools"])
        self.assertIn("filesystem", res["required_tools"])

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

    def test_format_tick_report_joins_lines(self):
        out = format_tick_report({"tick_report": ["line one", "line two", "  "]})
        self.assertEqual(out, "line one\nline two")

    def test_format_tick_report_silent_when_nothing_happened(self):
        self.assertEqual(format_tick_report({"tick_report": []}), "")
        self.assertEqual(format_tick_report({}), "")

    def test_format_output_renders_the_tick_report(self):
        out = format_output({"tick_report": ["AOC-01: published PR #9"]})
        self.assertEqual(out, "AOC-01: published PR #9")

    def test_format_output_is_silent_on_a_noop_tick(self):
        """Empty output is the signal to post nothing; a quiet queue stays quiet."""
        self.assertEqual(format_output({"tick_report": []}), "")

    def test_format_output_reports_the_error(self):
        out = format_output({"error_message": "Disk full", "tick_report": []})
        self.assertIn("Coding tick error: Disk full", out)

    def test_format_output_survives_a_non_dict(self):
        self.assertEqual(format_output("not a state"), "not a state")


if __name__ == '__main__':
    unittest.main()
