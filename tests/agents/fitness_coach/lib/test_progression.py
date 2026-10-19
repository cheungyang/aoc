import json
import unittest
from datetime import date, timedelta

from core.tooling import Report
from tests.agents.fitness_coach.lib.workout_fixture import EQUIPMENT_MD, LIBRARY
from agents.fitness_coach.lib.equipment import parse_equipment
from agents.fitness_coach.lib.progression import (
    NeedsPick, gap_after, merge_lines, next_line, parse_progression, prescribe,
)
from agents.fitness_coach.lib.rules import defaults

EQ = parse_equipment(EQUIPMENT_MD, "equipment.md", Report())
RULES = defaults()
BY_ID = {e["id"]: e for entries in LIBRARY.values() for e in entries}
TODAY = date(2026, 10, 16)
LEVER_CURL = {"id": "lever_curl", "name": "Lever Arm Curl", "primary_muscle": "Biceps", "equipment": ["Lever Arms", "Rack"],
              "role": "accessory", "tracking": "weight_reps", "rep_range": "8-12"}


def sets(*rows):
    return [dict(zip(("load", "reps", "signal"), r)) for r in rows]


def line(id, days_ago=3, **fields):
    return {"id": id, "last": (TODAY - timedelta(days=days_ago)).isoformat(), **fields}


class TestPrescribe(unittest.TestCase):
    def rx(self, id, progression_line=None, rules=RULES):
        return prescribe(BY_ID.get(id, LEVER_CURL), progression_line, TODAY, EQ, rules)

    def test_never_done_is_a_calibration_start(self):
        self.assertEqual(self.rx("bb_back_squat").text(), "5-8 @ 45 (new)")            # empty bar
        self.assertEqual(self.rx("db_goblet_squat").text(), "8-12 @ 5 (new)")          # lightest dial
        self.assertEqual(self.rx("landmine_goblet_squat").text(), "8-12 @ 0 (new)")    # empty sleeve
        self.assertEqual(self.rx("svend_press").text(), "10-15 @ 1.25 (new)")          # lightest plate
        self.assertEqual(self.rx("trx_pistol_squat").text(), "5-8/leg (new)")          # easy angle
        self.assertEqual(self.rx("plank").text(), "30-60s (new)")
        self.assertIsNone(self.rx("plank").days)

    def test_next_targets(self):
        squat = self.rx("bb_back_squat", line("bb_back_squat", load=135, reps=6, next_load=145))
        self.assertEqual((squat.text(), squat.days, squat.deload), ("5-8 @ 145", 3, None))
        self.assertEqual(self.rx("bb_bench_press", line("bb_bench_press", load=102.5, reps=6, next_load=102.5, next_reps=8)).text(), "8 @ 102.5")
        self.assertEqual(self.rx("pull_ups", line("pull_ups", reps=7, next_reps=8)).text(), "8")
        self.assertEqual(self.rx("plank", line("plank", secs=45, next_secs=50)).text(), "50s")
        self.assertEqual(self.rx("db_walking_lunge", line("db_walking_lunge", load=25, reps=10, next_load=30)).text(), "8-12/leg @ 30")

    def test_over_21_days_off_also_shows_the_target_10_percent_lighter_snapped_down(self):
        squat = self.rx("bb_back_squat", line("bb_back_squat", 30, load=135, reps=6, next_load=145))
        self.assertEqual((squat.days, squat.load, squat.deload.load), (30, 145, 130))
        self.assertEqual(squat.text(), "5-8 @ 145 (-10%: 5-8 @ 130)")
        self.assertEqual(self.rx("bb_bench_press", line("bb_bench_press", 22, load=115, reps=6, next_load=120, next_reps=7)).text(),
                         "7 @ 120 (-10%: 5-8 @ 102.5)")                                 # 108 is in a gap
        self.assertEqual(self.rx("db_goblet_squat", line("db_goblet_squat", 40, load=30, reps=10, next_load=30)).deload.load, 25)
        self.assertEqual(self.rx("pull_ups", line("pull_ups", 25, reps=8, next_reps=9)).text(), "9 (-10%: 8)")
        self.assertEqual(self.rx("plank", line("plank", 25, secs=45, next_secs=50)).text(), "50s (-10%: 45s)")
        self.assertIsNone(self.rx("bb_back_squat", line("bb_back_squat", 21, load=135, reps=6, next_load=145)).deload)

    def test_the_break_and_its_discount_come_from_rules(self):
        rules = {**RULES, "deload_after_days": 28, "deload_pct": 20}
        progression_line = line("bb_back_squat", 30, load=135, reps=6, next_load=145)
        self.assertEqual(self.rx("bb_back_squat", progression_line, rules).text(), "5-8 @ 145 (-20%: 5-8 @ 115)")
        self.assertIsNone(self.rx("bb_back_squat", line("bb_back_squat", 25, next_load=145), rules).deload)

    def test_an_unreadable_date_means_days_unknown_and_no_discount(self):
        rx = self.rx("bb_back_squat", {"id": "bb_back_squat", "last": "someday", "load": 135, "reps": 6, "next_load": 145})
        self.assertEqual((rx.days, rx.deload, rx.text()), (None, None, "5-8 @ 145"))


class TestNextLine(unittest.TestCase):
    def nxt(self, id, done, cut_short=False, pick=None, rules=RULES, report=None):
        return next_line(BY_ID.get(id, LEVER_CURL), done, TODAY, EQ, rules, cut_short=cut_short, pick=pick, report=report)

    def test_all_completed_steps_up(self):
        self.assertEqual(self.nxt("bb_bench_press", sets((135, 6, "completed"), (135, 6, "completed"))),
                         {"id": "bb_bench_press", "last": "2026-10-16", "load": 135, "reps": 6, "next_load": 140})
        self.assertEqual(self.nxt("bb_back_squat", sets(*[(135, 6, "completed")] * 3))["next_load"], 145)
        self.assertEqual(self.nxt("bb_deadlift", sets(*[(145, 5, "completed")] * 3))["next_load"], 155)  # Back, but a deadlift
        self.assertEqual(self.nxt("db_goblet_squat", sets(*[(25, 10, "completed")] * 3))["next_load"], 30)
        self.assertEqual(self.nxt("landmine_goblet_squat", sets(*[(25, 10, "completed")] * 3))["next_load"], 30)

    def test_any_up_or_down_holds_the_last_load(self):
        result = self.nxt("bb_back_squat", sets((125, 6, "up"), (135, 6, "completed"), (135, 5, "completed")))
        self.assertEqual((result["load"], result["reps"], result["next_load"]), (135, 5, 135))
        self.assertEqual(self.nxt("bb_back_squat", sets((135, 6, "completed"), (125, 6, "down")))["next_load"], 125)

    def test_a_gap_needs_the_coachs_pick(self):
        with self.assertRaises(NeedsPick) as caught:
            self.nxt("bb_bench_press", sets(*[(100, 6, "completed")] * 3))
        self.assertEqual(caught.exception.gap.as_dict(), {"from": 100, "below": 102.5, "above": 115, "top_reps": 8})
        self.assertEqual(str(caught.exception),
                         "one step up from 100 is 105, which can't be built (nothing loads between 102.5 and 115)")
        with self.assertRaises(NeedsPick):
            self.nxt("lever_curl", sets(*[(25, 9, "completed")] * 3))                 # 30 is unbuildable per arm

    def test_the_skills_gap_picks_give_todays_targets(self):
        """The weight below the gap with more reps, held until the reps top the range, then the jump."""
        below = self.nxt("bb_bench_press", sets(*[(100, 6, "completed")] * 3), pick=102.5)
        self.assertEqual(below, {"id": "bb_bench_press", "last": "2026-10-16", "load": 100, "reps": 6,
                                 "next_load": 102.5, "next_reps": 8})
        stuck = self.nxt("bb_bench_press", sets(*[(102.5, 7, "completed")] * 3), pick=102.5)
        self.assertEqual((stuck["next_load"], stuck["next_reps"]), (102.5, 8))
        jumped = self.nxt("bb_bench_press", sets(*[(102.5, 8, "completed")] * 3), pick=115)   # reps topped 5-8
        self.assertEqual((jumped["next_load"], "next_reps" in jumped), (115, False))
        lever = self.nxt("lever_curl", sets(*[(25, 9, "completed")] * 3), pick=28.75)
        self.assertEqual((lever["next_load"], lever["next_reps"]), (28.75, 11))

    def test_a_pick_is_snapped_down_to_a_buildable_weight(self):
        self.assertEqual(self.nxt("bb_bench_press", sets(*[(100, 6, "completed")] * 3), pick=105)["next_load"], 102.5)
        self.assertEqual(self.nxt("bb_bench_press", sets(*[(100, 6, "completed")] * 3), pick=30)["next_load"], 45)

    def test_a_pick_counts_without_a_gap_too(self):
        held = self.nxt("bb_back_squat", sets(*[(135, 6, "completed")] * 3), pick=135)   # lighter than the step up
        self.assertEqual((held["next_load"], held["next_reps"]), (135, 8))
        lighter = self.nxt("bb_back_squat", sets((135, 6, "up"), (145, 5, "completed")), pick=135)
        self.assertEqual((lighter["next_load"], lighter["next_reps"]), (135, 7))

    def test_the_heaviest_dial_holds_and_adds_reps(self):
        top = self.nxt("db_goblet_squat", sets(*[(50, 12, "completed")] * 3))
        self.assertEqual((top["next_load"], top["next_reps"]), (50, 13))

    def test_reps_and_time(self):
        self.assertEqual(self.nxt("pull_ups", [{"reps": 7, "signal": "completed"}] * 3)["next_reps"], 9)
        self.assertEqual(self.nxt("pull_ups", [{"reps": 9, "signal": "completed"}] * 3)["next_reps"], 10)
        self.assertEqual(self.nxt("pull_ups", [{"reps": 8, "signal": "up"}, {"reps": 10, "signal": "completed"}])["next_reps"], 10)
        self.assertEqual(self.nxt("plank", [{"secs": 45, "signal": "completed"}] * 3), {"id": "plank", "last": "2026-10-16", "secs": 45, "next_secs": 55})
        self.assertEqual(self.nxt("plank", [{"secs": 55, "signal": "completed"}] * 3)["next_secs"], 60)
        self.assertEqual(self.nxt("mountain_climber", [{"secs": 150, "signal": "completed"}])["next_secs"], 160)

    def test_the_steps_come_from_rules(self):
        rules = {**RULES, "rep_step": 3, "secs_step": 15}
        self.assertEqual(self.nxt("pull_ups", [{"reps": 5, "signal": "completed"}] * 3, rules=rules)["next_reps"], 8)
        self.assertEqual(self.nxt("pull_ups", [{"reps": 8, "signal": "completed"}] * 3, rules=rules)["next_reps"], 9)
        self.assertEqual(self.nxt("plank", [{"secs": 45, "signal": "completed"}] * 3, rules=rules)["next_secs"], 60)

    def test_cut_short_holds(self):
        self.assertEqual(self.nxt("bb_back_squat", sets((135, 6, "completed")), cut_short=True)["next_load"], 135)

    def test_explain_traces_each_target(self):
        report = Report()
        self.nxt("bb_back_squat", sets(*[(135, 6, "completed")] * 3), report=report)
        self.nxt("bb_bench_press", sets(*[(100, 6, "completed")] * 3), pick=105, report=report)
        self.nxt("plank", [{"secs": 55, "signal": "completed"}] * 3, report=report)
        self.assertEqual(report.traces, [
            "bb_back_squat next load 145 from 135 × 6 (every set ✅)",
            "bb_bench_press next load 102.5 from 100 × 6 (every set ✅, your pick 105, reps 6 → 8)",
            "plank next 60s from 55s (every set ✅: + 5; range tops at 60s)",
        ])


class TestGapAfter(unittest.TestCase):
    def test_gaps_one_step_up(self):
        bench, squat = BY_ID["bb_bench_press"], BY_ID["bb_back_squat"]
        self.assertEqual(gap_after(bench, 100, EQ, RULES).as_dict(), {"from": 100, "below": 102.5, "above": 115, "top_reps": 8})
        self.assertEqual(gap_after(bench, 102.5, EQ, RULES).as_dict(), {"from": 102.5, "below": 102.5, "above": 115, "top_reps": 8})
        self.assertIsNone(gap_after(squat, 145, EQ, RULES))
        self.assertIsNone(gap_after(bench, 170, EQ, RULES))                      # past the heaviest: nothing to jump to
        self.assertIsNone(gap_after(BY_ID["db_goblet_squat"], 25, EQ, RULES))    # dumbbells step to the next dial

    def test_no_weights_no_gap(self):
        self.assertIsNone(gap_after(BY_ID["pull_ups"], 0, EQ, RULES))


class TestFile(unittest.TestCase):
    def test_replace_in_place_append_new_keep_the_rest(self):
        lines = [
            '{"id":"plank","last":"2026-10-01","secs":40,"next_secs":45}',
            "not json, kept as it is",
            '{"id":"bb_back_squat","last":"2026-10-01","load":175,"reps":6,"next_load":185}',
        ]
        updates = {"bb_back_squat": {"id": "bb_back_squat", "last": "2026-10-16", "load": 185, "reps": 6, "next_load": 195},
                   "pull_ups": {"id": "pull_ups", "last": "2026-10-16", "reps": 7, "next_reps": 9}}
        merged = merge_lines(lines, updates)
        self.assertEqual(merged[:2], lines[:2])
        self.assertEqual(merged[2], '{"id":"bb_back_squat","last":"2026-10-16","load":185,"reps":6,"next_load":195}')
        self.assertEqual(json.loads(merged[3])["id"], "pull_ups")
        self.assertEqual(parse_progression(merged, "progression.jsonl", Report())["bb_back_squat"]["next_load"], 195)

    def test_lines_it_cannot_read_are_named(self):
        report = Report()
        found = parse_progression([
            '{"id":"plank","last":"2026-10-01","secs":40,"next_secs":45}',
            "plank 50s",
            '{"id":"plank","last":"2026-10-05","secs":45,"next_secs":50}',
            '{"id":"pull_ups","last":"soon","reps":7,"next_reps":9}',
        ], "progression.jsonl", report)
        self.assertEqual(found["plank"]["secs"], 40)                 # the first line counts
        self.assertEqual(report.issue_lines(), [
            "progression.jsonl: pull_ups: 'last' ('soon') isn't a date: days since unknown, no break discount",
            "progression.jsonl: 1 line not recognized",
            "progression.jsonl: plank has more than one line; the first one counts",
        ])
        self.assertEqual(report.issues[1].lines, ["plank 50s"])


if __name__ == "__main__":
    unittest.main()
