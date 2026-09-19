"""Tests for the registry/state join and summarisation.

Pure functions over fixture data, so these run without a network or a socket.
The fixtures encode the shapes that actually caused trouble against the real
instance: an entity whose area comes from its device, a disabled entity, and an
entity with no area at all.
"""
import unittest

from core.integrations.homeassistant import inventory


AREAS = [
    {"area_id": "garage", "name": "Garage"},
    {"area_id": "kitchen", "name": "Kitchen"},
]

DEVICES = [
    {"id": "dev1", "area_id": "garage"},
    {"id": "dev2", "area_id": "kitchen"},
    {"id": "dev3", "area_id": None},
]

ENTITIES = [
    # Area inherited from the device -- the common case.
    {"entity_id": "light.garage_main", "device_id": "dev1", "original_name": "Garage Main"},
    # Entity-level area_id overrides the device's.
    {"entity_id": "sensor.moved", "device_id": "dev1", "area_id": "kitchen", "name": "Moved Sensor"},
    # Disabled entities exist in the registry but never in /api/states.
    {"entity_id": "sensor.disabled", "device_id": "dev2", "disabled_by": "integration"},
    # Hidden but active.
    {"entity_id": "sensor.hidden", "device_id": "dev2", "hidden_by": "user"},
    # No device, no area.
    {"entity_id": "switch.orphan", "original_name": "Orphan"},
]

STATES = [
    {"entity_id": "light.garage_main", "state": "on", "attributes": {"friendly_name": "Garage Main"}},
    {"entity_id": "sensor.moved", "state": "21.5", "attributes": {}},
    {"entity_id": "sensor.hidden", "state": "3", "attributes": {}},
    {"entity_id": "switch.orphan", "state": "off", "attributes": {}},
]


def rows():
    return inventory.build(ENTITIES, DEVICES, AREAS, STATES)


class TestBuild(unittest.TestCase):
    def test_one_row_per_registry_entity(self):
        self.assertEqual(len(rows()), len(ENTITIES))

    def test_area_is_inherited_from_the_device(self):
        row = next(r for r in rows() if r["entity_id"] == "light.garage_main")
        self.assertEqual(row["area"], "Garage")

    def test_entity_area_overrides_the_device_area(self):
        # dev1 is in the Garage, but this entity declares Kitchen.
        row = next(r for r in rows() if r["entity_id"] == "sensor.moved")
        self.assertEqual(row["area"], "Kitchen")

    def test_entity_without_device_or_area_has_none(self):
        row = next(r for r in rows() if r["entity_id"] == "switch.orphan")
        self.assertIsNone(row["area"])

    def test_disabled_and_hidden_are_flagged(self):
        by_id = {r["entity_id"]: r for r in rows()}
        self.assertTrue(by_id["sensor.disabled"]["disabled"])
        self.assertFalse(by_id["sensor.disabled"]["hidden"])
        self.assertTrue(by_id["sensor.hidden"]["hidden"])
        self.assertFalse(by_id["sensor.hidden"]["disabled"])

    def test_state_is_merged_in_and_absent_for_disabled(self):
        by_id = {r["entity_id"]: r for r in rows()}
        self.assertEqual(by_id["light.garage_main"]["state"], "on")
        # Disabled entities have no state; the row must still exist.
        self.assertIsNone(by_id["sensor.disabled"]["state"])

    def test_name_falls_back_through_the_sources(self):
        by_id = {r["entity_id"]: r for r in rows()}
        self.assertEqual(by_id["sensor.moved"]["name"], "Moved Sensor")      # registry name
        self.assertEqual(by_id["switch.orphan"]["name"], "Orphan")           # original_name
        self.assertEqual(by_id["sensor.disabled"]["name"], "sensor.disabled")  # entity_id


class TestSummarise(unittest.TestCase):
    def test_totals_separate_active_from_disabled(self):
        summary = inventory.summarise(rows())
        self.assertEqual(summary["totals"]["entities"], 5)
        self.assertEqual(summary["totals"]["active"], 4)
        self.assertEqual(summary["totals"]["disabled"], 1)
        self.assertEqual(summary["totals"]["hidden"], 1)

    def test_disabled_entities_are_excluded_from_the_breakdown(self):
        summary = inventory.summarise(rows())
        # sensor.disabled is the only Kitchen sensor from dev2, so Kitchen's
        # remaining sensor count comes from sensor.moved alone.
        self.assertEqual(summary["by_area"]["Kitchen"]["sensor"], 2)

    def test_entities_without_an_area_are_grouped(self):
        summary = inventory.summarise(rows())
        self.assertIn("(unassigned)", summary["by_area"])

    def test_summary_size_is_independent_of_entity_count(self):
        """The point of the summary: 10x the entities must not mean 10x the output."""
        many = []
        for i in range(1000):
            many.append({"entity_id": f"sensor.s{i}", "device_id": "dev1"})
        small = inventory.summarise(inventory.build(ENTITIES, DEVICES, AREAS, STATES))
        large = inventory.summarise(inventory.build(many, DEVICES, AREAS, []))
        self.assertLessEqual(len(str(large)), len(str(small)) * 3)


class TestSearch(unittest.TestCase):
    def test_filters_by_domain(self):
        result = inventory.search(rows(), domain="light")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["entities"][0]["entity_id"], "light.garage_main")

    def test_filters_by_area_case_insensitively(self):
        self.assertEqual(inventory.search(rows(), area="garage")["total"], 1)
        self.assertEqual(inventory.search(rows(), area="Garage")["total"], 1)

    def test_query_matches_id_or_name(self):
        self.assertEqual(inventory.search(rows(), query="orphan")["total"], 1)
        self.assertEqual(inventory.search(rows(), query="Moved Sensor")["total"], 1)

    def test_disabled_are_excluded_unless_requested(self):
        self.assertEqual(inventory.search(rows(), query="disabled")["total"], 0)
        self.assertEqual(
            inventory.search(rows(), query="disabled", include_disabled=True)["total"], 1
        )

    def test_truncation_reports_the_real_total(self):
        many = [{"entity_id": f"sensor.s{i}", "device_id": "dev1"} for i in range(100)]
        result = inventory.search(inventory.build(many, DEVICES, AREAS, []), limit=10)
        self.assertEqual(result["total"], 100)
        self.assertEqual(result["returned"], 10)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["entities"]), 10)

    def test_no_filters_returns_everything_active(self):
        self.assertEqual(inventory.search(rows())["total"], 4)


if __name__ == "__main__":
    unittest.main()
