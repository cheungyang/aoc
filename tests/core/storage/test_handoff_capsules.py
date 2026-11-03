"""Parked hand-off capsules (core/storage/handoff_capsules.py): parked for a
teammate, taken once by its next turn in its channel, superseded by a newer
one, and never attached once expired."""
import unittest

from core.storage.handoff_capsules import KEEP_S, SWITCH, TTL_S, HandoffCapsuleStore, ParkedCapsule

NOW = 1_000_000.0


def park(store, to_agent="software-planner", to_channel="software-dev", capsule="<handoff_context/>", now=NOW,
         **kw):
    values = dict(from_agent="agent-designer", from_channel="agent-management", to_agent=to_agent,
                  to_channel=to_channel, capsule=capsule, now=now)
    values.update(kw)
    return store.park(**values)


class TestHandoffCapsuleStore(unittest.TestCase):

    def setUp(self):
        self.store = HandoffCapsuleStore()  # the suite points it at a temporary memory.db

    def test_a_parked_capsule_is_taken_with_everything_it_was_parked_with(self):
        capsule_id = park(self.store, brief="Spec the inbox scanner.")
        taken = self.store.take("software-planner", "software-dev", now=NOW + 60,
                                session_id="software-planner:tool:software-dev")
        self.assertEqual(taken, ParkedCapsule(
            id=capsule_id, created_at=NOW, from_agent="agent-designer", from_channel="agent-management",
            to_agent="software-planner", to_channel="software-dev", capsule="<handoff_context/>",
            expires_at=NOW + TTL_S, via=SWITCH, brief="Spec the inbox scanner.", consumed_at=NOW + 60,
            consumed_session="software-planner:tool:software-dev"))
        self.assertEqual(self.store.get(capsule_id), taken, "the claim is stored, not just returned")

    def test_it_is_taken_once(self):
        park(self.store)
        self.assertIsNotNone(self.store.take("software-planner", "software-dev", now=NOW + 1))
        self.assertIsNone(self.store.take("software-planner", "software-dev", now=NOW + 2))
        # Nor by another connection: the claim is in the file.
        self.assertIsNone(HandoffCapsuleStore(self.store.path).take("software-planner", "software-dev", now=NOW + 3))

    def test_only_the_teammate_in_its_channel_takes_it(self):
        park(self.store)
        self.assertIsNone(self.store.take("day-planner", "software-dev", now=NOW + 1))
        self.assertIsNone(self.store.take("software-planner", "day-planning", now=NOW + 1))
        self.assertIsNotNone(self.store.take("software-planner", "software-dev", now=NOW + 1))

    def test_channels_are_keyed_by_name_in_lower_case(self):
        park(self.store, to_channel="#Software-Dev")
        self.assertIsNotNone(self.store.take("software-planner", "software-dev", now=NOW + 1))

    def test_a_newer_capsule_supersedes_the_one_still_waiting(self):
        older = park(self.store, capsule="<older/>")
        newer = park(self.store, capsule="<newer/>", now=NOW + 30)
        self.assertEqual(self.store.get(older).superseded_at, NOW + 30)
        self.assertIsNone(self.store.get(newer).superseded_at)
        self.assertEqual(self.store.take("software-planner", "software-dev", now=NOW + 60).id, newer)
        self.assertIsNone(self.store.take("software-planner", "software-dev", now=NOW + 61),
                          "the superseded one never comes back")

    def test_a_taken_capsule_is_not_superseded(self):
        taken = park(self.store)
        self.store.take("software-planner", "software-dev", now=NOW + 1)
        park(self.store, now=NOW + 2)
        self.assertIsNone(self.store.get(taken).superseded_at)

    def test_an_expired_capsule_is_never_taken(self):
        park(self.store)
        self.assertIsNone(self.store.take("software-planner", "software-dev", now=NOW + TTL_S))
        self.assertIsNotNone(self.store.take("software-planner", "software-dev", now=NOW + TTL_S - 1))

    def test_old_rows_go_when_the_next_one_is_parked(self):
        old = park(self.store)
        park(self.store, to_agent="day-planner", to_channel="day-planning", now=NOW + KEEP_S + 1)
        self.assertIsNone(self.store.get(old))

    def test_the_ttl_is_two_hours(self):
        self.assertEqual(TTL_S, 2 * 3600)

    def test_the_store_reads_every_column_of_its_schema(self):
        with self.store.connect() as conn:
            columns = [r["name"] for r in conn.execute("PRAGMA table_info(handoff_capsules)")]
        self.assertEqual(sorted(columns), sorted(ParkedCapsule.__dataclass_fields__))

    def test_default_path_is_memory_db(self):
        from core.config.settings import Config
        self.assertEqual(HandoffCapsuleStore().path, Config().memory_db_path)


if __name__ == "__main__":
    unittest.main()
