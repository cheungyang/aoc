"""The tickers market-scout screens: list bullets, the lines it can't read, and the caller's own."""
import os
import shutil
import tempfile
import unittest

from agents.market_scout.lib.tickers import followed_tickers, list_path, parse_list, requested_tickers
from core.tooling import Report, ToolError
from core.config.settings import Config

PORTFOLIO = "# Stock Portfolio\n\n- KO\n- PEP\n- IBM\n"
WATCHLIST = "# Watchlist\n\n- XOM\n- CAT\n- JNJ\n- WMT\n- F\n- PEP\n"


class TestParseList(unittest.TestCase):

    def test_the_real_list_format(self):
        self.assertEqual(parse_list(PORTFOLIO), (["KO", "PEP", "IBM"], []))
        self.assertEqual(parse_list(WATCHLIST), (["XOM", "CAT", "JNJ", "WMT", "F", "PEP"], []))

    def test_decorated_bullets_and_symbol_shapes(self):
        text = "\n".join([
            "- **BRK.B** core holding",
            "* $IBM",
            "- [[CAT]]",
            "- [F](F.md)",
            "1. ^GSPC",
            "- [ ] BRK-B",
            "- KO: 10 shares",
        ])
        self.assertEqual(parse_list(text), (["BRK.B", "IBM", "CAT", "F", "^GSPC", "BRK-B", "KO"], []))

    def test_prose_and_non_tickers_come_back_raw(self):
        text = "# Watchlist\n\nXOM in prose\n- Consider adding energy\n- 2026 goals\n- xom\n"
        self.assertEqual(parse_list(text), ([], ["XOM in prose", "- Consider adding energy", "- 2026 goals", "- xom"]))

    def test_a_repeated_ticker_counts_once_and_is_not_an_unread_line(self):
        self.assertEqual(parse_list("- XOM\n- XOM\n"), (["XOM"], []))

    def test_frontmatter_headings_blanks_and_empty_bullets_are_skipped(self):
        text = "---\ntags: [stocks]\n---\n## Tech\n\n- CAT\n- \n  \n### Energy\n"
        self.assertEqual(parse_list(text), (["CAT"], []))

    def test_an_unclosed_frontmatter_fence_is_an_ordinary_line(self):
        self.assertEqual(parse_list("---\n- CAT\n"), (["CAT"], ["---"]))

    def test_a_note_under_a_ticker_is_not_a_ticker(self):
        self.assertEqual(parse_list("- PEP\n  - bought in 2024\n"), (["PEP"], ["  - bought in 2024"]))


class TestFollowedTickers(unittest.TestCase):

    def setUp(self):
        self.pkm = tempfile.mkdtemp()
        Config().pkm_dir = self.pkm
        self.addCleanup(setattr, Config(), "pkm_dir", None)
        self.addCleanup(shutil.rmtree, self.pkm, True)
        os.makedirs(os.path.join(self.pkm, "wallet", "stock"))
        self.report = Report()

    def write(self, name, text):
        with open(os.path.join(self.pkm, "wallet", "stock", name), "w", encoding="utf-8") as f:
            f.write(text)

    def test_portfolio_first_and_a_symbol_on_both_lists_counts_once(self):
        self.write("portfolio.md", PORTFOLIO)
        self.write("watchlist.md", WATCHLIST)
        self.assertEqual(followed_tickers(self.report), [
            ("KO", "portfolio"), ("PEP", "portfolio"), ("IBM", "portfolio"),
            ("XOM", "watchlist"), ("CAT", "watchlist"), ("JNJ", "watchlist"), ("WMT", "watchlist"), ("F", "watchlist"),
        ])
        self.assertEqual((self.report.status, self.report.issues), ("ok", []))
        self.assertEqual([(r.source, r.records, r.skipped) for r in self.report.reads], [
            ("pkm/wallet/stock/portfolio.md", 3, 0), ("pkm/wallet/stock/watchlist.md", 6, 0),
        ])

    def test_lines_it_cant_read_come_back_raw_and_make_it_partial(self):
        self.write("portfolio.md", PORTFOLIO)
        self.write("watchlist.md", "# Watchlist\n\n- XOM\n- xom\nConsider energy names\n")
        self.assertEqual([s for s, _ in followed_tickers(self.report)], ["KO", "PEP", "IBM", "XOM"])
        self.assertEqual(self.report.status, "partial")
        [issue] = self.report.issues
        self.assertEqual(issue.headline(), "pkm/wallet/stock/watchlist.md: 2 lines not recognized")
        self.assertEqual(issue.lines, ["- xom", "Consider energy names"])
        self.assertEqual(self.report.reads[1].skipped, 2)

    def test_a_missing_list_is_no_issue_and_the_other_list_is_screened(self):
        self.write("watchlist.md", "- XOM\n")
        self.assertEqual(followed_tickers(self.report), [("XOM", "watchlist")])
        self.assertEqual((self.report.status, self.report.issues), ("ok", []))
        self.assertEqual(self.report.traces, ["pkm/wallet/stock/portfolio.md: not found, so no tickers from it"])

    def test_empty_or_missing_lists_hold_no_tickers_and_no_issue(self):
        self.assertEqual(followed_tickers(self.report), [])  # both missing
        self.assertEqual((self.report.status, self.report.issues), ("ok", []))

        self.write("portfolio.md", "# Stock Portfolio\n")
        self.write("watchlist.md", "")
        report = Report()
        self.assertEqual(followed_tickers(report), [])  # both empty
        self.assertEqual((report.status, report.issues), ("ok", []))

    def test_list_path_is_in_the_pkm(self):
        self.assertEqual(os.path.realpath(list_path("watchlist")),
                         os.path.realpath(os.path.join(self.pkm, "wallet", "stock", "watchlist.md")))


class TestRequestedTickers(unittest.TestCase):

    def test_upper_case_each_once_with_no_list(self):
        self.assertEqual(requested_tickers(["xom", "$cat", "XOM"]), [("XOM", None), ("CAT", None)])
        self.assertEqual(requested_tickers("pep, ibm"), [("PEP", None), ("IBM", None)])

    def test_naming_no_symbol_is_refused(self):
        with self.assertRaisesRegex(ToolError, "'tickers' names no symbols"):
            requested_tickers([" ", ""])


if __name__ == "__main__":
    unittest.main()
