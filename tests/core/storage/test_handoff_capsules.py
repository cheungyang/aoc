"""Parked hand-off capsules (core/storage/handoff_capsules.py): parked for a
teammate, taken once by its next turn in its channel, superseded by a newer
one, and never attached once expired. A pass's waits a day, and what waits can
be read without claiming it."""
import unittest

from core.storage.handoff_capsules import (KEEP_S, PASS, PASS_TTL_S, SWITCH, TTL_S, HandoffCapsuleStore,
                                           ParkedCapsule)

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


class TestPassCapsules(unittest.TestCase):
    """A <pass>'s capsule: parked like a switch's, but waiting a day, since the
    user stays with the agent that passed it and reaches the teammate later."""

    def setUp(self):
        self.store = HandoffCapsuleStore()

    def test_a_pass_waits_a_day(self):
        self.assertEqual(PASS_TTL_S, 24 * 3600)
        capsule_id = park(self.store, via=PASS, brief="Book the dentist.")
        parked = self.store.get(capsule_id)
        self.assertEqual((parked.via, parked.expires_at), (PASS, NOW + PASS_TTL_S))
        self.assertIsNotNone(self.store.waiting("software-planner", "software-dev", now=NOW + PASS_TTL_S - 1))
        self.assertIsNone(self.store.take("software-planner", "software-dev", now=NOW + PASS_TTL_S))
        self.assertEqual(self.store.take("software-planner", "software-dev", now=NOW + PASS_TTL_S - 1).id, capsule_id)

    def test_a_capsule_can_be_kept_until_later_than_its_ttl_but_never_shorter(self):
        """A switch's capsule that carries a waiting pass's brief keeps the pass's time."""
        kept = park(self.store, until=NOW + PASS_TTL_S)
        self.assertEqual(self.store.get(kept).expires_at, NOW + PASS_TTL_S)
        shorter = park(self.store, to_agent="day-planner", to_channel="day-planning", until=NOW + 60)
        self.assertEqual(self.store.get(shorter).expires_at, NOW + TTL_S)


class TestWaitingCapsules(unittest.TestCase):
    """What still waits, read without claiming it: for one teammate, for all,
    and an agent's passes that haven't reached their teammates yet."""

    def setUp(self):
        self.store = HandoffCapsuleStore()

    def test_waiting_is_what_take_would_claim_and_claims_nothing(self):
        park(self.store, capsule="<older/>")
        newer = park(self.store, capsule="<newer/>", brief="Spec it.", now=NOW + 30)
        found = self.store.waiting("software-planner", "#Software-Dev", now=NOW + 60)
        self.assertEqual((found.id, found.capsule, found.brief, found.consumed_at),
                         (newer, "<newer/>", "Spec it.", None))
        self.assertEqual(self.store.waiting("software-planner", "software-dev", now=NOW + 61), found, "still there")
        self.assertEqual(self.store.take("software-planner", "software-dev", now=NOW + 62).id, newer)
        self.assertIsNone(self.store.waiting("software-planner", "software-dev", now=NOW + 63), "taken")

    def test_nothing_waits_for_another_teammate_or_channel_or_once_expired(self):
        park(self.store)
        self.assertIsNone(self.store.waiting("day-planner", "software-dev", now=NOW + 1))
        self.assertIsNone(self.store.waiting("software-planner", "day-planning", now=NOW + 1))
        self.assertIsNone(self.store.waiting("software-planner", "software-dev", now=NOW + TTL_S))

    def test_waiting_all_lists_every_capsule_still_waiting_oldest_first(self):
        first = park(self.store, to_agent="day-planner", to_channel="day-planning", via=PASS)
        second = park(self.store, now=NOW + 10)
        taken = park(self.store, to_agent="meal-planner", to_channel="meal-planning", now=NOW + 20)
        self.store.take("meal-planner", "meal-planning", now=NOW + 30)
        park(self.store, to_agent="fitness-coach", to_channel="fitness-coach", now=NOW - TTL_S)  # expired by now
        self.assertEqual([c.id for c in self.store.waiting_all(now=NOW + 60)], [first, second])
        self.assertNotIn(taken, [c.id for c in self.store.waiting_all(now=NOW + 60)])
        self.assertEqual([c.id for c in self.store.waiting_all(now=NOW + 60)], [first, second], "none was claimed")

    def test_open_passes_are_an_agents_passes_still_waiting(self):
        to_daisy = park(self.store, to_agent="day-planner", to_channel="day-planning", via=PASS, brief="Dentist.")
        to_ramsay = park(self.store, to_agent="meal-planner", to_channel="meal-planning", via=PASS, now=NOW + 10)
        park(self.store, now=NOW + 20)  # a switch, not a pass
        park(self.store, from_agent="day-planner", from_channel="day-planning", to_agent="meal-planner",
             to_channel="fitness", via=PASS, now=NOW + 30)  # another agent's pass
        park(self.store, from_channel="general", to_agent="fitness-coach", to_channel="fitness-coach", via=PASS,
             now=NOW + 40)  # passed from another channel
        self.assertEqual([c.id for c in self.store.open_passes("agent-designer", "#Agent-Management", now=NOW + 60)],
                         [to_daisy, to_ramsay])
        self.assertEqual(len(self.store.open_passes("agent-designer", now=NOW + 60)), 3, "from every channel")

    def test_a_pass_is_no_longer_open_once_taken_superseded_or_expired(self):
        park(self.store, to_agent="day-planner", to_channel="day-planning", via=PASS)
        park(self.store, to_agent="meal-planner", to_channel="meal-planning", via=PASS)
        park(self.store, to_agent="fitness-coach", to_channel="fitness-coach", via=PASS, now=NOW - PASS_TTL_S + 30)
        self.store.take("day-planner", "day-planning", now=NOW + 1)
        park(self.store, to_agent="meal-planner", to_channel="meal-planning", now=NOW + 2)  # the switch that follows
        self.assertEqual(self.store.open_passes("agent-designer", "agent-management", now=NOW + 60), [])


if __name__ == "__main__":
    unittest.main()
