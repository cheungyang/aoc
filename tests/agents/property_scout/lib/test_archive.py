"""The archive of evaluated listings: TOUR reports and the PASS log."""
import unittest

from core.tooling import Report
from tests.agents.property_scout import REPORT_MD, PkmCase
from agents.property_scout.lib.archive import Archive, address_key, load_archive


class TestArchive(PkmCase):

    def test_a_report_matches_by_address_however_the_street_is_spelled(self):
        self.write("properties/120_Maple_Dr_Springfield.md", REPORT_MD)
        archive = load_archive(Report())
        self.assertEqual(archive.find({"address": "120 Maple Dr, Springfield, IL 62701"}),
                         "properties/120_Maple_Dr_Springfield.md")
        self.assertIsNotNone(archive.find({"address": "120 Maple Drive, Springfield, IL 62701"}))
        self.assertIsNone(archive.find({"address": "12 Maple Dr, Springfield, IL 62701"}))
        self.assertIsNone(archive.find({"address": "120 Maple Dr, Shelbyville, IL 62702"}))

    def test_a_file_name_alone_matches_even_with_the_state_and_zip(self):
        self.write("properties/88_Cedar_Rd_Ogdenville_IL_62703.md", "# notes\n")
        self.assertEqual(load_archive(Report()).find({"address": "88 Cedar Rd, Ogdenville, IL 62703"}),
                         "properties/88_Cedar_Rd_Ogdenville_IL_62703.md")

    def test_a_report_link_matches_by_zpid(self):
        self.write("properties/X.md", "- **Link**: https://www.zillow.com/homedetails/15-Birch-Ave/10000002_zpid/\n")
        archive = load_archive(Report())
        self.assertEqual(archive.find({"address": "elsewhere", "zpid": 10000002}), "properties/X.md")
        self.assertEqual(archive.find({"address": "", "url": "https://www.zillow.com/homedetails/10000002_zpid/"}),
                         "properties/X.md")

    def test_the_pass_log(self):
        self.write("passed.md", "\n".join([
            "# Passed listings",
            "",
            "- 2026-10-16 | 123 Main St, Shelbyville, IL 62702 | https://www.zillow.com/homedetails/111_zpid/ | busy road",
            "- 2026-10-16 | 9 Oak Ct, Ogdenville, IL 62703 | no link | as-is",
            "a stray line | 77 Elm St, Ogdenville, IL 62703 | x",
        ]) + "\n")
        report = Report(explain=True)
        archive = load_archive(report)
        self.assertEqual(archive.find({"address": "1 Other St, Shelbyville, IL 62702", "zpid": 111}), "passed.md line 3")
        self.assertEqual(archive.find({"address": "123 Main Street, Shelbyville, IL 62702"}), "passed.md line 3")
        self.assertEqual(archive.find({"address": "9 Oak Court, Ogdenville, IL 62703"}), "passed.md line 4")
        # A line that isn't a PASS line comes back raw, and its listing isn't counted as evaluated.
        self.assertIsNone(archive.find({"address": "77 Elm St, Ogdenville, IL 62703"}))
        self.assertEqual(report.status, "partial")
        self.assertEqual(report.issue_lines(), ["pkm/wallet/real-estate/passed.md: 1 line not recognized"])
        self.assertEqual(report.issues[0].lines, ["a stray line | 77 Elm St, Ogdenville, IL 62703 | x"])
        self.assertIn("pkm/wallet/real-estate/passed.md (modified", report.render_explain())
        self.assertIn("2 records, 1 line skipped", report.render_explain())

    def test_nothing_archived_yet(self):
        report = Report()
        archive = load_archive(report)
        self.assertEqual((archive.zpids, archive.keys), ({}, {}))
        self.assertIsNone(archive.find({"address": ""}))
        self.assertEqual(report.status, "ok")

    def test_address_key(self):
        self.assertEqual(address_key("15 Birch Avenue, Capital City, IL 62704"), "15birchavecapitalcity")


class TestPassLog(unittest.TestCase):

    def test_reads_pass_lines_and_returns_the_rest(self):
        archive = Archive()
        read, unread = archive.add_pass_log("\n".join([
            "---",
            "- 2026-10-16 | 123 Main St, Shelbyville, IL 62702 | https://www.zillow.com/homedetails/111_zpid/ | busy road",
            "* https://www.zillow.com/homedetails/222_zpid/",
            "- a note without an address",
            "Listings I passed on:",
        ]))
        self.assertEqual(read, 2)
        self.assertEqual(unread, ["- a note without an address", "Listings I passed on:"])
        self.assertEqual(archive.find({"address": "", "zpid": 222}), "passed.md line 3")
