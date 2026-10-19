"""Tests for the controllable-entity list behind home-steward's device_list.

Fixtures are made up, shaped like a real registry and states: a switch_as_x
light hiding the switch it wraps, a plug's config switch, a dead cloud
duplicate that is always unavailable, a YAML group with no registry entry, and
a device-area entity next to one with its own area.
"""
import unittest
from datetime import datetime
from unittest import mock

from agents.home_steward.lib.homeassistant import entity_index, guards
from agents.home_steward.lib.homeassistant.entity_index import Entry

AREAS = [
    {"area_id": "kitchen", "name": "Kitchen"},
    {"area_id": "backyard", "name": "Backyard"},
    {"area_id": "living_room", "name": "Living Room"},
]

DEVICES = [
    {"id": "dev_kitchen_relay", "area_id": "kitchen"},
    {"id": "dev_patio_plug", "area_id": "backyard"},
    {"id": "dev_tv_stick", "area_id": "kitchen"},
]

ENTITIES = [
    {"entity_id": "light.kitchen_ceiling", "device_id": "dev_kitchen_relay", "platform": "switch_as_x"},
    {"entity_id": "switch.kitchen_ceiling", "device_id": "dev_kitchen_relay", "hidden_by": "integration"},
    {"entity_id": "switch.patio_plug", "device_id": "dev_patio_plug", "platform": "mqtt"},
    {"entity_id": "switch.patio_plug_led", "device_id": "dev_patio_plug", "entity_category": "config"},
    {"entity_id": "switch.kitchen_ceiling_2", "platform": "cloud_media"},
    # The device sits in the kitchen; the entity was moved to the living room.
    {"entity_id": "media_player.tv_stick_01", "device_id": "dev_tv_stick", "area_id": "living_room"},
    {"entity_id": "climate.thermostat", "platform": "mqtt", "disabled_by": "user"},
    {"entity_id": "sensor.kitchen_temperature", "device_id": "dev_kitchen_relay"},
]


def state(entity_id, value="off", name=None):
    return {"entity_id": entity_id, "state": value, "attributes": {"friendly_name": name} if name else {}}


STATES = [
    state("light.kitchen_ceiling", name="Kitchen ceiling"),
    state("switch.kitchen_ceiling", name="Kitchen ceiling"),
    state("switch.patio_plug", "on", name="Patio string lights"),
    state("switch.patio_plug_led", "on", name="Patio string lights LED"),
    state("switch.kitchen_ceiling_2", "unavailable", name="Kitchen ceiling"),
    state("media_player.tv_stick_01", name="Den TV"),
    state("sensor.kitchen_temperature", "21.5", name="Kitchen temperature"),
    # A YAML group: live, but not in the entity registry.
    state("switch.yard_lights_group", name="Yard lights group"),
]

REFRESHED = datetime(2026, 10, 18, 20, 0)


def built():
    return entity_index.build(ENTITIES, DEVICES, AREAS, STATES)


class TestBuild(unittest.TestCase):

    def test_keeps_only_what_a_person_would_name(self):
        self.assertEqual(
            [e.entity_id for e in built()],
            [
                "switch.patio_plug",            # Backyard
                "light.kitchen_ceiling",        # Kitchen
                "media_player.tv_stick_01",     # Living Room
                "switch.yard_lights_group",     # no area, last
            ],
        )

    def test_drops_hidden_config_unavailable_disabled_and_other_domains(self):
        ids = {e.entity_id for e in built()}
        self.assertNotIn("switch.kitchen_ceiling", ids)       # hidden by switch_as_x
        self.assertNotIn("switch.patio_plug_led", ids)        # entity_category config
        self.assertNotIn("switch.kitchen_ceiling_2", ids)     # unavailable duplicate
        self.assertNotIn("climate.thermostat", ids)           # disabled: no state at all
        self.assertNotIn("sensor.kitchen_temperature", ids)   # not a control domain

    def test_area_comes_from_the_entity_then_its_device(self):
        by_id = {e.entity_id: e for e in built()}
        self.assertEqual(by_id["light.kitchen_ceiling"].area, "Kitchen")
        self.assertEqual(by_id["media_player.tv_stick_01"].area, "Living Room")
        self.assertEqual(by_id["switch.yard_lights_group"].area, "")

    def test_name_is_the_friendly_name(self):
        by_id = {e.entity_id: e for e in built()}
        self.assertEqual(by_id["switch.patio_plug"].name, "Patio string lights")

    def test_no_blocked_domain_is_indexed(self):
        self.assertFalse(set(entity_index.INDEX_DOMAINS) & guards.BLOCKED_DOMAINS)


class TestRender(unittest.TestCase):

    def test_one_line_per_area_with_names_only_where_the_id_lacks_them(self):
        self.assertEqual(
            entity_index.render(built()),
            'Backyard: switch.patio_plug "Patio string lights"\n'
            "Kitchen: light.kitchen_ceiling\n"
            'Living Room: media_player.tv_stick_01 "Den TV"\n'
            "No area: switch.yard_lights_group",
        )

    def test_a_duplicate_suffix_or_repeated_area_word_still_says_the_name(self):
        entries = [
            Entry("Living Room", "media_player.living_room_tv_2", "Living Room TV"),
            Entry("Garage", "switch.garage_garage_fan", "Garage Fan"),
        ]
        self.assertEqual(
            [entity_index._item(e) for e in entries],
            ["media_player.living_room_tv_2", "switch.garage_garage_fan"],
        )

    def test_the_index_is_capped_and_says_how_much_it_left_out(self):
        entries = [Entry("Garage", f"switch.plug_{i:03d}", f"Plug {i:03d}") for i in range(250)]
        text = entity_index.render(entries)
        self.assertEqual(text.count("switch.plug_"), entity_index.MAX_ENTRIES)
        self.assertTrue(text.endswith("(+50 more not listed)"))

    def test_nothing_renders_empty(self):
        self.assertEqual(entity_index.render([]), "")


class TestFetch(unittest.TestCase):

    def test_reads_everything_over_one_websocket(self):
        ws = mock.MagicMock()
        ws.__enter__.return_value = ws
        ws.entities.return_value = ENTITIES
        ws.devices.return_value = DEVICES
        ws.areas.return_value = AREAS
        ws.command.return_value = STATES
        with mock.patch.object(entity_index, "HomeAssistantWebSocket", return_value=ws) as cls:
            entries = entity_index.fetch()
        cls.assert_called_once_with(timeout=entity_index.FETCH_TIMEOUT_SECONDS)
        ws.command.assert_called_once_with("get_states")
        self.assertEqual(entries, built())


class TestDeviceFile(unittest.TestCase):
    """DEVICES.md, as render_file writes it and parse_file reads it back."""

    def test_a_heading_per_area_and_a_line_per_device_with_its_name(self):
        self.assertEqual(
            entity_index.render_file(built(), REFRESHED),
            f"{entity_index.NOTICE}\n"
            "\n"
            "Refreshed: 2026-10-18 20:00\n"
            "\n"
            "## Backyard\n"
            "- switch.patio_plug: Patio string lights\n"
            "\n"
            "## Kitchen\n"
            "- light.kitchen_ceiling: Kitchen ceiling\n"
            "\n"
            "## Living Room\n"
            "- media_player.tv_stick_01: Den TV\n"
            "\n"
            "## No area\n"
            "- switch.yard_lights_group: Yard lights group\n",
        )

    def test_the_first_line_says_who_writes_it_and_that_edits_are_overwritten(self):
        first = entity_index.render_file([], REFRESHED).splitlines()[0]
        self.assertIn("device_list", first)
        self.assertIn("weekly", first)
        self.assertIn("overwritten", first)

    def test_reads_back_what_it_wrote(self):
        saved = entity_index.parse_file(entity_index.render_file(built(), REFRESHED))
        self.assertEqual(saved.refreshed, "2026-10-18 20:00")
        self.assertEqual(saved.entries, built())
        self.assertEqual(saved.unrecognized, [])

    def test_an_empty_list_still_says_when_it_was_refreshed(self):
        saved = entity_index.parse_file(entity_index.render_file([], REFRESHED))
        self.assertEqual((saved.refreshed, saved.entries, saved.unrecognized), ("2026-10-18 20:00", [], []))

    def test_names_with_quotes_colons_and_newlines_stay_on_their_line(self):
        entry = Entry("Den", "media_player.tv_1", 'Den 65" TV: main\nfloor')
        saved = entity_index.parse_file(entity_index.render_file([entry], REFRESHED))
        self.assertEqual(saved.entries, [Entry("Den", "media_player.tv_1", 'Den 65" TV: main floor')])

    def test_a_device_line_without_a_name_shows_its_id_alone(self):
        saved = entity_index.parse_file("## Porch\n- light.porch\n")
        self.assertEqual(saved.entries, [Entry("Porch", "light.porch", "")])
        self.assertEqual(entity_index.render(saved.entries), "Porch: light.porch")

    def test_lines_it_doesnt_recognize_come_back_as_written(self):
        text = entity_index.render_file(built(), REFRESHED) + (
            "Patio plug: broken\n"
            "### Spare plugs\n"
            "- Light.Porch: Porch light\n"
            "Refreshed: last Sunday\n"
        )
        saved = entity_index.parse_file(text)
        self.assertEqual(
            saved.unrecognized,
            ["Patio plug: broken", "### Spare plugs", "- Light.Porch: Porch light", "Refreshed: last Sunday"],
        )
        self.assertEqual(saved.entries, built())
        self.assertEqual(saved.refreshed, "2026-10-18 20:00")

    def test_without_a_refreshed_line_when_is_unknown(self):
        saved = entity_index.parse_file("## Kitchen\n- light.kitchen_ceiling: Kitchen ceiling\n")
        self.assertIsNone(saved.refreshed)
        self.assertEqual(saved.entries, [Entry("Kitchen", "light.kitchen_ceiling", "Kitchen ceiling")])


if __name__ == "__main__":
    unittest.main()
