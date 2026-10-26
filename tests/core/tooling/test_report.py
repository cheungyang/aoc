"""The Report and the envelope: status and issues first, never a quietly partial answer."""
import os
import tempfile
import unittest

from core.tooling import FAILED, OK, PARTIAL, DataProblem, Report
from core.tooling.report import MAX_ISSUES, format_tool_response, render_envelope
from core.config.settings import Config


class TestReport(unittest.TestCase):

    def test_status_follows_the_worst_issue(self):
        report = Report()
        self.assertEqual(report.status, OK)
        report.problem("pkm/a.md", "one row unreadable")
        self.assertEqual(report.status, PARTIAL)
        report.missing("pkm/b.md", "file not found")
        self.assertEqual(report.status, FAILED)

    def test_unparsed_lines_come_back_raw_and_blank_lines_dont_count(self):
        report = Report()
        report.unparsed("pkm/kitchen/INVENTORY.md", ["| 冷凍 | ??? |", "", "  "])
        self.assertEqual(report.issue_lines(), ["pkm/kitchen/INVENTORY.md: 1 line not recognized"])
        self.assertIn("    | 冷凍 | ??? |", report.render_issues())
        report.unparsed("pkm/a.md", ["x", "y"])
        self.assertEqual(report.issue_lines()[1], "pkm/a.md: 2 lines not recognized")
        self.assertIsNone(Report().unparsed("x", ["", " "]))  # nothing to report: no issue

    def test_raw_lines_and_issues_are_capped_but_counted(self):
        report = Report()
        report.unparsed("pkm/a.md", [f"line {i}" for i in range(50)])
        rendered = report.render_issues()
        self.assertIn("line 39", rendered)
        self.assertNotIn("line 40", rendered)
        self.assertIn("... and 10 more lines", rendered)

        many = Report()
        for i in range(MAX_ISSUES + 5):
            many.problem("pkm/a.md", f"row {i} unreadable")
        self.assertIn("- ... and 5 more issues", many.render_issues())
        self.assertEqual(len(many.issue_lines()), MAX_ISSUES + 5)

    def test_raise_if_failed_carries_the_report(self):
        report = Report()
        report.problem("pkm/a.md", "minor")
        report.raise_if_failed()  # partial: no raise
        report.missing("pkm/criteria.md", "names no state")
        with self.assertRaises(DataProblem) as caught:
            report.raise_if_failed()
        self.assertIs(caught.exception.report, report)
        self.assertEqual(str(caught.exception), "pkm/a.md: minor; pkm/criteria.md: names no state")
        self.assertEqual(str(DataProblem(Report())), "required data is missing")

    def test_merge_and_explain(self):
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
            path = f.name
        self.addCleanup(os.remove, path)
        inner = Report()
        inner.read(path, records=3, skipped=1)
        inner.read("Google Health (sleep)")
        inner.trace("recovery[legs]: stored 40 + 2/h x 10 h -> 60")
        inner.problem("pkm/a.md", "row 3 unreadable")
        outer = Report().merge(inner)
        self.assertIs(outer.merge(outer), outer)  # merging itself adds nothing
        self.assertEqual(outer.issue_lines(), ["pkm/a.md: row 3 unreadable"])
        text = outer.render_explain()
        self.assertRegex(text, r"Read:\n- .*\.md \(modified \d{4}-\d\d-\d\d \d\d:\d\d, 3 records, 1 line skipped\)")
        self.assertIn("\n- Google Health (sleep)\n", text)
        self.assertIn("Computed:\n- recovery[legs]: stored 40", text)
        self.assertEqual(Report().render_explain(), "Nothing recorded.")


class TestSources(unittest.TestCase):

    def test_an_opened_pkm_file_is_named_as_the_agent_names_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            Config().pkm_dir = tmp
            self.addCleanup(setattr, Config(), "pkm_dir", None)
            path = os.path.join(tmp, "wallet", "kitchen", "INVENTORY.md")
            os.makedirs(os.path.dirname(path))
            open(path, "w").close()
            report = Report()
            report.missing(path, "no 冷凍 section")
            report.read(path, records=0)
            self.assertEqual(report.issue_lines(), ["pkm/wallet/kitchen/INVENTORY.md: no 冷凍 section"])
            self.assertEqual(report.reads[0].source, "pkm/wallet/kitchen/INVENTORY.md")


class TestEnvelope(unittest.TestCase):

    def test_status_and_issues_precede_the_payload(self):
        report = Report()
        report.problem("pkm/a.md", "row 3 unreadable")
        out = render_envelope("demo", report, '{"x": 1}')
        self.assertEqual(out, (
            "<demo_response>\n"
            "  <status>partial</status>\n"
            "  <issues>- pkm/a.md: row 3 unreadable</issues>\n"
            '  <payload>{"x": 1}</payload>\n'
            "  <errors>None</errors>\n"
            "</demo_response>"
        ))

    def test_an_error_means_failed_and_explain_is_opt_in(self):
        out = render_envelope("demo", Report(), errors="Error: boom")
        self.assertIn("<status>failed</status>", out)
        self.assertIn("<issues>None</issues>", out)
        self.assertIn("<errors>Error: boom</errors>", out)
        self.assertNotIn("<explain>", out)
        explained = render_envelope("demo", Report(explain=True))
        self.assertIn("<explain>Nothing recorded.</explain>", explained)
        self.assertLess(explained.index("<explain>"), explained.index("<payload>"))

    def test_a_required_issue_alone_means_failed(self):
        report = Report()
        report.missing("pkm/criteria.md", "names no state")
        out = render_envelope("demo", report)
        self.assertIn("<status>failed</status>", out)
        self.assertIn("<issues>- pkm/criteria.md: names no state (required)</issues>", out)



class TestSharedToolEnvelope(unittest.TestCase):
    """format_tool_response: the envelope a shared tool returns, without a Report."""

    def test_format_tool_response(self):
        response = format_tool_response("test_tool", "test_payload", "test_errors")
        self.assertIn("<test_tool_response>", response)
        self.assertIn("<payload>test_payload</payload>", response)
        self.assertIn("<errors>test_errors</errors>", response)


if __name__ == "__main__":
    unittest.main()
