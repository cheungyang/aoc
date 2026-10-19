"""Tests for the audit news: what counts as a new problem, and the findings text.

Fixtures are made up, in the reference home's real shapes: system-log entries as
`system_log/list` returns them, states with `last_changed` reset by a restart,
and the compressed rows of `history/history_during_period`.
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from agents.home_steward.lib.homeassistant import audit

TZ = ZoneInfo("America/Los_Angeles")
LAST_AUDIT = datetime(2026, 10, 15, 8, 0, tzinfo=TZ).timestamp()      # Thu 08:00
RESTART = datetime(2026, 10, 12, 16, 27, tzinfo=TZ).timestamp()       # Mon 16:27


def at(hours_after_audit: float) -> float:
    return LAST_AUDIT + hours_after_audit * 3600


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def log_entry(name, level="ERROR", first=at(1), last=None, count=1, message="Error fetching devices data"):
    return {
        "name": name, "level": level, "message": [message], "source": ["coordinator.py", 312],
        "exception": "", "count": count, "first_occurred": first, "timestamp": last or first,
    }


def state(entity_id, value, changed, name=None):
    return {
        "entity_id": entity_id, "state": value, "last_changed": iso(changed),
        "attributes": {"friendly_name": name} if name else {},
    }


LOG = [
    log_entry("homeassistant.components.cloud_media.coordinator", first=at(4.5), last=at(9), count=27),
    # Recurring since before the last audit: already reported, counted as ongoing.
    log_entry("homeassistant.components.recorder.core", first=at(-28), last=at(9), count=3661,
              message="SQLAlchemyError error processing task CommitTask()"),
    log_entry("zeroconf", level="WARNING", first=at(2)),
    log_entry("homeassistant.helpers.storage", level="CRITICAL", first=at(6),
              message="Error writing config for core.restore_state: [Errno 28] No space left on device"),
    # Old and quiet since: neither new nor recurring.
    log_entry("metno", first=at(-30), last=at(-29)),
]

STATES = [
    # Offline since the restart, before the last audit: old news.
    state("light.desk_lamp", "unavailable", RESTART, "Desk lamp"),
    # Dropped after the last audit.
    state("switch.plug_1", "unavailable", at(3), "Plug 1"),
    state("switch.plug_1_led", "unavailable", at(3), "Plug 1 LED"),
    # Back after the last audit: a recovery never wakes the audit.
    state("media_player.radio", "idle", at(2), "Radio"),
    # Changed after the audit, but unavailable both then and now (it flapped).
    state("switch.camera_power", "unavailable", at(5), "Camera power"),
    # Created after the audit and unavailable from the start: no history at the audit.
    state("sensor.mug_battery", "unavailable", at(7), "Mug battery"),
    # Was reporting values at the audit, unknown now.
    state("sensor.kitchen_temperature", "unknown", at(8), "Kitchen temperature"),
    # Created after the audit and unknown, as a button is until first pressed.
    state("button.plug_restart", "unknown", at(7), "Plug restart"),
    # Changed, fine then and now.
    state("light.kitchen_ceiling", "on", at(1), "Kitchen ceiling"),
]

THEN = {
    "switch.plug_1": "on",
    "switch.plug_1_led": "on",
    "switch.camera_power": "unavailable",
    "sensor.kitchen_temperature": "21.5",
}

AREAS = [{"area_id": "backyard", "name": "Backyard"}, {"area_id": "kitchen", "name": "Kitchen"},
         {"area_id": "living_room", "name": "Living Room"}]
DEVICES = [
    {"id": "plug", "area_id": "backyard", "name": "Plug 1", "name_by_user": None},
    {"id": "thermo", "area_id": "kitchen", "name": "Temperature sensor", "name_by_user": "Kitchen sensor"},
]
ENTITIES = [
    {"entity_id": "switch.plug_1", "device_id": "plug", "platform": "mqtt"},
    {"entity_id": "switch.plug_1_led", "device_id": "plug", "platform": "mqtt"},
    {"entity_id": "sensor.kitchen_temperature", "device_id": "thermo", "platform": "mqtt"},
    {"entity_id": "light.desk_lamp", "area_id": "living_room", "platform": "cloud_media"},
    {"entity_id": "switch.lamp_2", "platform": "cloud_media"},
    {"entity_id": "switch.fan_2", "platform": "cloud_media"},
]


class TestErrors(unittest.TestCase):

    def test_errors_first_seen_after_the_last_audit_newest_first(self):
        names = [e["name"] for e in audit.new_errors(LOG, LAST_AUDIT)]
        self.assertEqual(names, ["homeassistant.helpers.storage", "homeassistant.components.cloud_media.coordinator"])

    def test_a_recurring_error_already_reported_and_warnings_stay_quiet(self):
        names = {e["name"] for e in audit.new_errors(LOG, LAST_AUDIT)}
        self.assertNotIn("homeassistant.components.recorder.core", names)
        self.assertNotIn("zeroconf", names)

    def test_older_errors_that_occurred_again_are_counted(self):
        self.assertEqual(audit.recurring_errors(LOG, LAST_AUDIT), 1)


class TestProblemEntities(unittest.TestCase):

    def test_only_problems_that_changed_since_the_audit_are_looked_up(self):
        self.assertEqual(audit.problem_candidates(STATES, LAST_AUDIT), [
            "button.plug_restart",
            "sensor.kitchen_temperature",
            "sensor.mug_battery",
            "switch.camera_power",
            "switch.plug_1",
            "switch.plug_1_led",
        ])

    def test_new_problems_only(self):
        self.assertEqual(audit.new_problem_entities(STATES, THEN, LAST_AUDIT), [
            "sensor.kitchen_temperature",        # had a value, unknown now
            "sensor.mug_battery",                # new device that never came up
            "switch.plug_1",
            "switch.plug_1_led",
        ])

    def test_a_restart_does_not_make_old_problems_new(self):
        # A restart resets last_changed for everything; the recorder still says
        # the entity was unavailable at the audit.
        restarted = [state("light.desk_lamp", "unavailable", at(2), "Desk lamp")]
        self.assertEqual(audit.new_problem_entities(restarted, {"light.desk_lamp": "unavailable"}, LAST_AUDIT), [])

    def test_an_entity_that_starts_out_unknown_is_no_problem(self):
        self.assertNotIn("button.plug_restart", audit.new_problem_entities(STATES, THEN, LAST_AUDIT))


class FakeWebSocket:
    """Answers the commands `check` sends, and records them."""

    def __init__(self, log=LOG, states=STATES, then=THEN):
        self.answers = {
            "system_log/list": log,
            "get_states": states,
            "history/history_during_period": {e: [{"s": s, "lu": LAST_AUDIT}] for e, s in then.items()},
        }
        self.sent = []
        self.entities = mock.Mock(return_value=ENTITIES)
        self.devices = mock.Mock(return_value=DEVICES)
        self.areas = mock.Mock(return_value=AREAS)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def command(self, command_type, **params):
        self.sent.append((command_type, params))
        return self.answers[command_type]


class TestStatesAt(unittest.TestCase):

    def test_reads_a_one_second_window_at_the_audit(self):
        ws = FakeWebSocket()
        result = audit.states_at(ws, LAST_AUDIT, ["switch.camera_power"])
        command, params = ws.sent[0]
        self.assertEqual(command, "history/history_during_period")
        start = datetime.fromisoformat(params["start_time"])
        self.assertEqual(start.timestamp(), LAST_AUDIT)
        self.assertEqual(datetime.fromisoformat(params["end_time"]) - start, timedelta(seconds=1))
        self.assertEqual(params["entity_ids"], ["switch.camera_power"])
        self.assertTrue(params["include_start_time_state"])
        self.assertTrue(params["no_attributes"])
        self.assertEqual(result["switch.camera_power"], "unavailable")

    def test_nothing_to_look_up_sends_nothing(self):
        ws = FakeWebSocket()
        self.assertEqual(audit.states_at(ws, LAST_AUDIT, []), {})
        self.assertEqual(ws.sent, [])


class TestCheck(unittest.TestCase):

    def run_check(self, ws, since=LAST_AUDIT):
        with mock.patch.object(audit, "HomeAssistantWebSocket", return_value=ws):
            return audit.check(since, TZ)

    def test_new_problems_are_described_by_device_and_area(self):
        news = self.run_check(FakeWebSocket())
        self.assertEqual(
            news.summary,
            "2 new error(s), 4 newly unavailable or unknown; ongoing: 2 unavailable, 1 recurring error(s)",
        )
        self.assertEqual(news.findings, "\n".join([
            "New problems since Thu 08:00.",
            "New errors (2), newest first:",
            "- CRITICAL homeassistant.helpers.storage (first Thu 14:00): Error writing config for "
            "core.restore_state: [Errno 28] No space left on device",
            "- ERROR homeassistant.components.cloud_media.coordinator (first Thu 12:30, last Thu 17:00, x27): "
            "Error fetching devices data",
            "Newly unavailable or unknown (4 entities):",
            "- Backyard: Plug 1 (2 entities)",
            "- Kitchen: Kitchen temperature (sensor.kitchen_temperature)",
            "- No area: Mug battery (sensor.mug_battery)",
            "Still ongoing from before: 2 entities unavailable, 1 older error(s) still recurring.",
        ]))

    def test_only_problem_candidates_reach_the_recorder(self):
        ws = FakeWebSocket()
        self.run_check(ws)
        history = [params for command, params in ws.sent if command == "history/history_during_period"]
        self.assertEqual(len(history), 1)
        self.assertNotIn("light.desk_lamp", history[0]["entity_ids"])
        self.assertNotIn("media_player.radio", history[0]["entity_ids"])

    def test_a_recovery_alone_wakes_nobody_and_skips_the_registries(self):
        quiet = [s for s in STATES if s["entity_id"] in ("light.desk_lamp", "media_player.radio")]
        ws = FakeWebSocket(log=LOG[1:3], states=quiet, then={})
        news = self.run_check(ws)
        self.assertEqual(news.findings, "")
        self.assertEqual(
            news.summary,
            "0 new error(s), 0 newly unavailable or unknown; ongoing: 1 unavailable, 1 recurring error(s)",
        )
        ws.entities.assert_not_called()


class TestFindingsText(unittest.TestCase):

    def places(self, states=STATES):
        return audit.Places(ENTITIES, DEVICES, AREAS, states)

    def test_entities_without_a_device_are_grouped_by_integration(self):
        states = STATES + [
            state("switch.lamp_2", "unavailable", RESTART, "Lamp"),
            state("switch.fan_2", "unavailable", RESTART, "Fan"),
        ]
        lines = audit._entity_lines(["switch.lamp_2", "switch.fan_2"], self.places(states))
        self.assertEqual(lines, ["- No area: cloud_media (2 entities)"])

    def test_long_sections_are_capped_and_counted(self):
        errors = [log_entry(f"custom_components.c{i}", first=at(i / 10)) for i in range(25)]
        text = audit.findings(errors, [], 0, 0, self.places(), LAST_AUDIT, TZ)
        self.assertEqual(text.count("- ERROR "), audit.MAX_LINES)
        self.assertIn("- +5 more", text)

    def test_long_messages_are_cut_and_flattened(self):
        line = audit._error_line(log_entry("x", message="Traceback\n  " + "y" * 400), TZ)
        self.assertNotIn("\n", line)
        self.assertTrue(line.endswith("..."))
        self.assertLess(len(line), audit.MESSAGE_CHARS + 60)


if __name__ == "__main__":
    unittest.main()
