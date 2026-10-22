"""SQLite schemas stored as files (core/db/), applied by name."""
import sqlite3
import tempfile
import os
import unittest

from core.db import apply_schema, load_schema


class TestSchemas(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "memory.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_channel_inbox_schema_applies_and_is_idempotent(self):
        conn = sqlite3.connect(self.path)
        try:
            apply_schema(conn, "channel_inbox")
            apply_schema(conn, "channel_inbox")  # every connection applies it
            columns = [row[1] for row in conn.execute("PRAGMA table_info(channel_inbox)")]
        finally:
            conn.close()
        self.assertEqual(columns, ["channel", "agent_id", "posted_at", "source", "schedule_id", "label", "speech",
                                   "question", "excerpt", "priority", "status", "status_at", "status_via"])

    def test_the_inbox_store_uses_it(self):
        from core.inbox.store import SCHEMA, _COLUMNS
        self.assertEqual(SCHEMA, "channel_inbox")
        for column in _COLUMNS:
            self.assertIn(column, load_schema(SCHEMA))

    def test_every_category_is_checked(self):
        ddl = load_schema("channel_inbox")
        for check in ("source IN ('SCHEDULE', 'REPLY', 'LATE_REPLY')", "priority IN ('LOW', 'DEFAULT', 'HIGHEST')",
                      "status IN ('UNREAD', 'AWAITING_REPLY', 'DONE', 'EXPIRED')",
                      "status_via IN ('VOICE', 'TYPED', 'REACTION')"):
            self.assertIn(check, ddl)


if __name__ == "__main__":
    unittest.main()
