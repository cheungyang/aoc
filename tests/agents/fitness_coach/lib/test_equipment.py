import os
import unittest

from core.tooling import Report
from tests.agents.fitness_coach.lib.workout_fixture import EQUIPMENT_MD
from agents.fitness_coach.lib.equipment import (
    implement_for, is_loadable, ladder, parse_equipment, snap_down, snap_up, station_for, step_up,
)
from agents.fitness_coach.lib.rules import defaults

REAL_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "pkm", "wallet", "exercise", "equipment.md")
RULES = defaults()


def steps(low, high, step):
    return {round(low + i * step, 4) for i in range(int(round((high - low) / step)) + 1)}


def parse(text):
    report = Report()
    return parse_equipment(text, "equipment.md", report), report


BARBELL = steps(45, 102.5, 2.5) | steps(115, 172.5, 2.5)
LEVER = steps(0, 28.75, 1.25) | steps(35, 63.75, 1.25)


class TestParse(unittest.TestCase):
    def setUp(self):
        self.eq, self.report = parse(EQUIPMENT_MD)

    def test_the_whole_file_reads(self):
        self.assertEqual(self.report.issues, [])

    def test_barbell_totals_skip_the_unbuildable_gaps(self):
        barbell = self.eq.loads["Barbell"]
        self.assertEqual(set(barbell), BARBELL)
        for gap in (105, 107.5, 110, 112.5):
            self.assertFalse(is_loadable(barbell, gap), gap)
        self.assertEqual((barbell[0], barbell[-1]), (45, 172.5))

    def test_landmine_lever_arms_dumbbells_and_plates(self):
        self.assertEqual(set(self.eq.loads["Landmine"]), steps(0, 127.5, 1.25))
        self.assertEqual(set(self.eq.loads["Lever Arms"]), LEVER)
        self.assertEqual(self.eq.loads["Dumbbells"], (5, 10, 15, 20, 25, 30, 40, 50))
        plates = self.eq.loads["Plates"]  # every total the plate pile makes
        self.assertEqual((plates[0], plates[-1], len(plates)), (1.25, 127.5, 102))

    def test_items_without_loads_are_ignored(self):
        self.assertEqual(set(self.eq.loads), {"Barbell", "Landmine", "Lever Arms", "Dumbbells", "Plates"})

    def test_stations(self):
        self.assertEqual(list(self.eq.stations), ["Rack", "Landmine", "Lever Arms", "Suspension", "Floor"])
        self.assertIn("Pull-Up Bar", self.eq.stations["Rack"])

    @unittest.skipUnless(os.path.exists(REAL_FILE), "no real equipment.md")
    def test_the_real_file_reads_cleanly(self):
        with open(REAL_FILE, encoding="utf-8") as f:
            real, report = parse(f.read())
        self.assertEqual(report.issues, [])
        self.assertLessEqual({"Barbell", "Dumbbells"}, set(real.loads))
        self.assertTrue(real.stations)

    def test_empty_text_says_what_is_missing(self):
        eq, report = parse("")
        self.assertEqual((eq.loads, eq.stations), ({}, {}))
        self.assertEqual(report.issue_lines(), [
            "equipment.md: no '## Loading' section: loads are not snapped to buildable weights",
            "equipment.md: no '## Stations' section: every station shows as 'Any'",
        ])

    def test_lines_it_cannot_read_come_back_raw(self):
        eq, report = parse("\n".join([
            "## Loading",
            "- **Barbell**: 45 lb bar.",
            "  - Loadable totals (2.5 lb steps): 45–102.5.",
            "- **Dumbbells**: adjustable pair, per hand.",   # a load implement with no weights
            "- Kettlebell 35 lb",                            # not a `- **Name**:` bullet
            "Bars are knurled.",                             # a note
            "## Stations",
            "| Station | Equipment |",
            "|---|---|",
            "| Rack | Barbell, Rack |",
            "| Garage |",
        ]))
        self.assertEqual((set(eq.loads), list(eq.stations)), ({"Barbell"}, ["Rack"]))
        self.assertEqual(report.status, "partial")
        [issue] = report.issues
        self.assertEqual(issue.headline(), "equipment.md: 3 lines not recognized")
        self.assertEqual(issue.lines, ["- Kettlebell 35 lb", "- **Dumbbells**: adjustable pair, per hand.", "| Garage |"])

    def test_sections_without_weights_or_rows(self):
        eq, report = parse("## Loading\n- **TRX, bodyweight**: progress with reps.\n## Stations\nNo table yet.\n")
        self.assertEqual((eq.loads, eq.stations), ({}, {}))
        self.assertEqual(report.issue_lines(), [
            "equipment.md: '## Loading' lists no loadable weights: loads are not snapped to buildable weights",
            "equipment.md: '## Stations' has no table rows: every station shows as 'Any'",
        ])


class TestLookups(unittest.TestCase):
    def setUp(self):
        self.eq, _ = parse(EQUIPMENT_MD)
        self.barbell = self.eq.loads["Barbell"]
        self.dumbbells = self.eq.loads["Dumbbells"]

    def test_snapping(self):
        self.assertEqual(snap_down(self.barbell, 166.5), 165)
        self.assertEqual(snap_down(self.barbell, 110), 102.5)
        self.assertEqual(snap_up(self.barbell, 107.5), 115)
        self.assertIsNone(snap_down(self.barbell, 40))
        self.assertIsNone(snap_up(self.barbell, 175))
        self.assertEqual(snap_down(self.dumbbells, 27), 25)

    def test_step_up(self):
        self.assertEqual(step_up("Barbell", self.barbell, 135, False, RULES), 140)
        self.assertEqual(step_up("Barbell", self.barbell, 145, True, RULES), 155)
        self.assertEqual(step_up("Dumbbells", self.dumbbells, 25, False, RULES), 30)
        self.assertIsNone(step_up("Dumbbells", self.dumbbells, 50, False, RULES))
        self.assertEqual(step_up("Landmine", self.eq.loads["Landmine"], 25, True, RULES), 30)

    def test_the_steps_come_from_rules(self):
        rules = {**RULES, "barbell_step_upper": 2.5, "barbell_step_lower": 5.0, "plate_step": 2.5}
        self.assertEqual(step_up("Barbell", self.barbell, 135, False, rules), 137.5)
        self.assertEqual(step_up("Barbell", self.barbell, 145, True, rules), 150)
        self.assertEqual(step_up("Landmine", self.eq.loads["Landmine"], 25, True, rules), 27.5)
        self.assertEqual(ladder("Barbell", self.barbell, 145, True, rules), [135, 140, 145, 150, 155])

    def test_ladder_jumps_gaps_and_stops_at_the_ends(self):
        self.assertEqual(ladder("Barbell", self.barbell, 145, True, RULES), [125, 135, 145, 155, 165])
        self.assertEqual(ladder("Barbell", self.barbell, 100, False, RULES), [90, 95, 100, 115, 120])
        self.assertEqual(ladder("Barbell", self.barbell, 45, True, RULES, down=0), [45, 55, 65])
        self.assertEqual(ladder("Barbell", self.barbell, 170, False, RULES), [160, 165, 170, 172.5])
        self.assertEqual(ladder("Dumbbells", self.dumbbells, 40, False, RULES), [25, 30, 40, 50])

    def test_ladder_through_a_heavier_start(self):
        """With a break discount the climb runs from the lighter start past the heavier one."""
        self.assertEqual(ladder("Barbell", self.barbell, 135, True, RULES, through=145),
                         [115, 125, 135, 145, 155, 165])

    def test_implement_precedence(self):
        self.assertEqual(implement_for(["Landmine", "Barbell"]), "Landmine")
        self.assertEqual(implement_for(["Barbell", "Rack", "Plates"]), "Barbell")
        self.assertEqual(implement_for(["Pull-Up Bar", "Dumbbells"]), "Dumbbells")
        self.assertEqual(implement_for(["Bench", "Plates"]), "Plates")
        self.assertIsNone(implement_for(["TRX"]))

    def test_station_for(self):
        stations = self.eq.stations
        cases = {
            ("Barbell", "Rack"): "Rack",
            ("Landmine", "Barbell"): "Landmine",
            ("Lever Arms", "Rack"): "Lever Arms",
            ("Bench", "Dumbbells"): "Floor",
            ("Pull-Up Bar", "Dumbbells"): "Rack",
            ("Barbell", "Yoga Mat"): "Rack",
            ("Dumbbells", "Plates"): "Floor",
            ("TRX", "Yoga Mat"): "Suspension",
            (): "Any",
        }
        for equipment, expected in cases.items():
            self.assertEqual(station_for(list(equipment), stations), expected, equipment)


if __name__ == "__main__":
    unittest.main()
