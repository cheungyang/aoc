"""The channel inbox table: one row per channel, enums checked, expiry on read."""
import sqlite3
import time
import unittest

from core.storage.channel_inbox import _COLUMNS, InboxStore
from core.storage.channel_inbox import TTL_S, InboxRow, Priority, Source, Status, Via


def row(channel="day-planning", posted_at=None, **kw):
    base = dict(channel=channel, agent_id="day-planner", posted_at=time.time() if posted_at is None else posted_at,
                source=Source.SCHEDULE, label="your morning check-in")
    base.update(kw)
    return InboxRow(**base)


class TestStore(unittest.TestCase):

    def setUp(self):
        self.store = InboxStore()  # the suite points it at a temporary memory.db

    def test_a_row_round_trips_with_its_enums(self):
        original = row(speech="Daisy here.", question="How's your energy?", priority=Priority.HIGHEST,
                       schedule_id="day-planner:morning-plan", excerpt="Good morning!")
        self.store.put(original)
        self.assertEqual(self.store.get("day-planning"), original)
        self.assertIsNone(self.store.get("general"))

    def test_one_row_per_channel_the_newer_post_replaces_it(self):
        self.store.put(row(posted_at=100.0, label="first"))
        self.store.put(row(posted_at=200.0, source=Source.REPLY, label="second"))
        rows = self.store.rows(now=300.0)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].source, rows[0].label), (Source.REPLY, "second"))

    def test_an_ignore_post_is_never_a_row(self):
        with self.assertRaises(ValueError):
            self.store.put(row(priority=Priority.IGNORE))

    def test_the_store_writes_every_column_of_its_schema(self):
        self.assertEqual(InboxStore.schema, ("channel_inbox",))
        with self.store.connect() as conn:
            columns = [r["name"] for r in conn.execute("PRAGMA table_info(channel_inbox)")]
        self.assertEqual(columns, list(_COLUMNS))

    def test_the_table_checks_every_category(self):
        with self.store.connect() as conn:  # creates the table
            for column, value in (("status", "READ"), ("source", "PUSH"), ("priority", "IGNORE"), ("status_via", "EMAIL")):
                with self.subTest(column=column), self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        f"INSERT INTO channel_inbox (channel, agent_id, posted_at, source, {column}) VALUES (?, ?, ?, ?, ?)"
                        if column != "source" else
                        "INSERT INTO channel_inbox (channel, agent_id, posted_at, source) VALUES (?, ?, ?, ?)",
                        ("x", "a", 1.0, "SCHEDULE", value) if column != "source" else ("x", "a", 1.0, value),
                    )

    def test_unfinished_rows_oldest_first_and_expired_ones_skipped(self):
        now = 1_000_000.0
        self.store.put(row("day-planning", posted_at=now - 60))
        self.store.put(row("topic-research", posted_at=now - 3600, status=Status.AWAITING_REPLY, question="Math?"))
        self.store.put(row("fitness-coach", posted_at=now - TTL_S - 1))
        self.store.put(row("general", posted_at=now - 30, status=Status.DONE))

        self.assertEqual([r.channel for r in self.store.unfinished(now)], ["topic-research", "day-planning"])
        # Expiry is written when read: the old row now says so.
        self.assertIs(self.store.get("fitness-coach").status, Status.EXPIRED)

    def test_a_status_change_can_be_pinned_to_one_post(self):
        self.store.put(row(posted_at=100.0))
        self.assertFalse(self.store.set_status("day-planning", Status.DONE, Via.VOICE, posted_at=99.0, now=150.0))
        self.assertTrue(self.store.set_status("day-planning", Status.DONE, Via.VOICE, posted_at=100.0, now=150.0))
        stored = self.store.get("day-planning")
        self.assertEqual((stored.status, stored.status_via, stored.status_at), (Status.DONE, Via.VOICE, 150.0))

    def test_a_status_change_only_moves_unfinished_rows_by_default(self):
        self.store.put(row(posted_at=100.0, status=Status.DONE))
        self.assertFalse(self.store.set_status("day-planning", Status.DONE, Via.TYPED))

    def test_posted_before_bounds_which_post_a_change_is_about(self):
        self.store.put(row(posted_at=1000.0))
        self.assertFalse(self.store.set_status("day-planning", Status.DONE, Via.REACTION, posted_before=999.0))
        self.assertTrue(self.store.set_status("day-planning", Status.DONE, Via.REACTION, posted_before=1000.0))

    def test_speech_is_stored_only_for_the_same_post(self):
        self.store.put(row(posted_at=100.0, source=Source.REPLY, label=None))
        self.assertFalse(self.store.set_speech("day-planning", 99.0, "Old.", None))
        self.assertTrue(self.store.set_speech("day-planning", 100.0, "Merge them?", "Merge them?"))
        self.assertEqual(self.store.get("day-planning").question, "Merge them?")


if __name__ == "__main__":
    unittest.main()
