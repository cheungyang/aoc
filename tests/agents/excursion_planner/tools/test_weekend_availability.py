import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core.config.settings import Config
from tests.helpers import envelope, execution_context
from tools.gog import GogError
from agents.excursion_planner.tools.weekend_availability import weekend_availability

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 16, 17, 56, tzinfo=LA)
SOCCER = {
    "summary": "Soccer practice",
    "start": {"dateTime": "2026-10-24T09:00:00-07:00"},
    "end": {"dateTime": "2026-10-24T11:00:00-07:00"},
}
GOG_EVENTS = {"events": [SOCCER], "nextPageToken": ""}


@patch("agents.excursion_planner.tools.weekend_availability.get_local_now", return_value=NOW)
@patch("tools.gog.list_events", return_value=GOG_EVENTS)
class TestWeekendAvailabilityTool(unittest.TestCase):

    def setUp(self):
        # RULES.md is read from the PKM: point it at an empty temporary one.
        self.pkm = tempfile.mkdtemp(prefix="excursion-pkm-")
        Config().pkm_dir = self.pkm
        self.addCleanup(setattr, Config(), "pkm_dir", None)
        self.addCleanup(shutil.rmtree, self.pkm, True)

    def call(self, agent_id="excursion-planner", **args):
        with execution_context(agent_id=agent_id):
            return envelope(weekend_availability.invoke(args))

    def write_rules(self, text):
        folder = os.path.join(self.pkm, "agents", "excursion-planner")
        os.makedirs(folder)
        with open(os.path.join(folder, "RULES.md"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_one_read_for_the_whole_range(self, list_events, _now):
        out = self.call(weeks=3)
        list_events.assert_called_once()
        start, end = list_events.call_args.args
        self.assertEqual((start, end), (datetime(2026, 10, 17, 7, tzinfo=timezone.utc), datetime(2026, 11, 2, 8, tzinfo=timezone.utc)))
        self.assertEqual(list_events.call_args.kwargs["max_results"], 300)
        self.assertEqual((out["status"], out["issues"], out["errors"]), ("ok", "None", "None"))
        self.assertNotIn("explain", out)
        result = json.loads(out["payload"])
        self.assertEqual(result["free_time"], "08:00–22:00, gaps of 1 h or more")
        weekends = result["weekends"]
        self.assertEqual(len(weekends), 3)
        self.assertEqual(weekends[1]["days"][0]["events"], ["09:00–11:00 Soccer practice"])
        self.assertEqual(weekends[1]["days"][0]["free"], ["08:00–09:00", "11:00–22:00"])
        self.assertEqual(weekends[2]["note"], "Sun Nov 1: clocks fall back 1 h at 02:00 (PDT → PST)")

    def test_weeks_is_clamped(self, list_events, _now):
        self.assertEqual(len(json.loads(self.call(weeks=40)["payload"])["weekends"]), 8)
        self.assertEqual(len(json.loads(self.call(weeks=0)["payload"])["weekends"]), 1)

    def test_more_events_than_fetched_is_partial(self, list_events, _now):
        list_events.return_value = {**GOG_EVENTS, "nextPageToken": "abc"}
        out = self.call()
        self.assertEqual(out["status"], "partial")
        self.assertEqual(out["issues"], "- Google Calendar (primary): it holds more than the 300 events fetched; some may be missing")
        self.assertEqual(len(json.loads(out["payload"])["weekends"]), 3)

    def test_an_unreadable_event_is_partial_and_the_rest_is_planned(self, list_events, _now):
        list_events.return_value = {"events": [SOCCER, {"summary": "Dinner", "start": {"dateTime": "Saturday 7pm"}}]}
        out = self.call()
        self.assertEqual(out["status"], "partial")
        self.assertEqual(out["issues"], "- Google Calendar (primary): 1 line not recognized\n"
                                        '    {"summary": "Dinner", "start": {"dateTime": "Saturday 7pm"}}')
        saturday = json.loads(out["payload"])["weekends"][1]["days"][0]
        self.assertEqual(saturday["events"], ["09:00–11:00 Soccer practice"])

    def test_a_calendar_that_cant_be_read_fails(self, list_events, _now):
        list_events.side_effect = GogError("gog calendar events failed: token expired")
        out = self.call()
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["issues"], "- Google Calendar (primary): gog calendar events failed: token expired (required)")
        self.assertEqual((out["payload"], out["errors"]), ("", "None"))

    def test_a_reply_with_no_event_list_fails(self, list_events, _now):
        list_events.return_value = {"data": [SOCCER]}
        out = self.call()
        self.assertEqual((out["status"], out["payload"]), ("failed", ""))
        self.assertIn("gog's reply holds no event list", out["issues"])

    def test_rules_md_sets_the_hours_and_the_shortest_gap(self, list_events, _now):
        self.write_rules("- free_from: 10:00\n- free_until: 20:00\n- min_gap_hours: 2\n")
        out = self.call(weeks=2)
        self.assertEqual(out["status"], "ok")
        result = json.loads(out["payload"])
        self.assertEqual(result["free_time"], "10:00–20:00, gaps of 2 h or more")
        self.assertEqual(result["weekends"][1]["days"][0]["free"], ["11:00–20:00"])

    def test_a_rules_md_value_that_doesnt_read_is_partial_and_keeps_the_default(self, list_events, _now):
        self.write_rules("- free_from: 8am\n- min_gap_hours: an hour\n")
        out = self.call()
        self.assertEqual(out["status"], "partial")
        self.assertIn("- pkm/agents/excursion-planner/RULES.md: free_from: '8am' isn't a time like 08:00; using 08:00",
                      out["issues"])
        self.assertIn("- pkm/agents/excursion-planner/RULES.md: line 2: 'min_gap_hours: an hour' can't be read",
                      out["issues"])
        self.assertEqual(json.loads(out["payload"])["free_time"], "08:00–22:00, gaps of 1 h or more")

    def test_explain_lists_the_calendar_read_and_the_arithmetic(self, list_events, _now):
        out = self.call(weeks=2, explain=True)
        self.assertEqual(out["explain"], "\n".join([
            "Read:",
            "- Google Calendar (primary) (1 record)",
            "Computed:",
            "- Google Calendar (primary): asked for up to 200 events from 2026-10-17T07:00:00Z to 2026-10-26T07:00:00Z",
            "- Sat Oct 17: 14 h free = 08:00–22:00 (14 h), counting 08:00–22:00",
            "- Sun Oct 18: 14 h free = 08:00–22:00 (14 h), counting 08:00–22:00",
            "- Sat Oct 24: 12 h free = 08:00–09:00 (1 h) + 11:00–22:00 (11 h), counting 08:00–22:00",
            "- Sun Oct 25: 14 h free = 08:00–22:00 (14 h), counting 08:00–22:00",
        ]))

    def test_needs_an_execution_context(self, list_events, _now):
        out = envelope(weekend_availability.invoke({}))
        self.assertEqual(out["status"], "failed")
        self.assertIn("no active execution context", out["errors"])
        list_events.assert_not_called()

    def test_an_agent_without_the_grant_is_refused(self, list_events, _now):
        out = self.call(agent_id="meal-planner")
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["errors"], "Error: Agent 'meal-planner' does not have permission to use weekend_availability.")
        list_events.assert_not_called()


if __name__ == "__main__":
    unittest.main()
