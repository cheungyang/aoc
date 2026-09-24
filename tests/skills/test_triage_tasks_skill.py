"""The triage_tasks skill hands the agent literal SQL. These tests extract every
```sql block from SKILL.md and run it against a seeded tasks.db through the same
read-only path task_query uses, so a typo or wrong column in the skill fails CI
instead of costing a wasted agent turn.
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.knowledge.tasks.db import execute_read_sql, get_connection, init_db

SKILL_MD = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "skills", "triage_tasks", "SKILL.md"))


def _sql_blocks():
    with open(SKILL_MD, encoding="utf-8") as f:
        text = f.read()
    return [b.strip() for b in re.findall(r"```sql\s*(.*?)```", text, re.DOTALL)]


def _find(blocks, needle):
    matches = [b for b in blocks if needle in b]
    assert len(matches) == 1, f"expected exactly one SQL block containing {needle!r}, got {len(matches)}"
    return matches[0]


class TestTriageTasksSkillSql(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = get_connection(os.path.join(self.tmp.name, "tasks.db"))
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        rows = [
            # id, title, priority, tags, source, line
            ("t1", "Read DDIA ch 3", "🔼", ["a/learn", "p/aoc"], "projects/aoc.md", 1),
            ("t2", "Fix scheduler bug", None, ["a/fix"], "projects/aoc.md", 2),
            ("t3", "Call plumber", None, [], "Inbox.md", 1),
            ("t4", "Book flights", None, ["a/book", "p/travel"], "Inbox.md", 2),
            ("t5", "Write memo", "⏫", ["a/write", "p/aoc"], "projects/aoc.md", 3),
            ("t6", "Learn Go", None, ["a/learn", "p/aoc"], "O'Brien.md", 1),
        ]
        for tid, title, prio, tags, source, line in rows:
            self.conn.execute(
                "INSERT INTO tasks (id, title, status, priority, tags, source, source_type, "
                "line_number, raw_line, content_hash, updated_date) "
                "VALUES (?, ?, 'todo', ?, ?, ?, 'vault', ?, ?, 'h', '2026-09-23')",
                (tid, title, prio, json.dumps(tags), source, line, f"- [ ] {title}"),
            )
        self.conn.commit()
        self.blocks = _sql_blocks()

    def _run(self, sql, limit=50):
        results, err = execute_read_sql(self.conn, sql, limit=limit)
        self.assertIsNone(err, f"{err}\n{sql}")
        return results

    def test_every_block_is_read_only_select(self):
        self.assertGreaterEqual(len(self.blocks), 4)
        for block in self.blocks:
            self.assertTrue(block.upper().startswith("SELECT"), block)

    def test_action_tag_index_is_distinct_and_compact(self):
        sql = _find(self.blocks, "LIKE 'a/%'")
        rows = self._run(sql, limit=200)
        tags = {r["tag"]: r["n"] for r in rows}
        self.assertEqual(tags, {"a/learn": 2, "a/fix": 1, "a/book": 1, "a/write": 1})
        # Only tag + count come back -- no task bodies.
        self.assertEqual(set(rows[0].keys()), {"tag", "n"})

    def test_next_untriaged_task_respects_priority_tags_and_skips(self):
        sql = _find(self.blocks, "<SKIPPED_IDS>")
        first = self._run(sql.replace("<SKIPPED_IDS>", "''"), limit=1)
        # t3 (Inbox.md line 1) has no tags and no priority; t4 is fully tagged.
        self.assertEqual([r["id"] for r in first], ["t3"])

        after_skip = self._run(sql.replace("<SKIPPED_IDS>", "'t3'"), limit=1)
        self.assertEqual([r["id"] for r in after_skip], ["t2"])

        remaining = self._run(sql.replace("<SKIPPED_IDS>", "'t3', 't2'"), limit=1)
        self.assertEqual(remaining, [])

    def test_same_file_precedent_is_scoped_to_source(self):
        sql = _find(self.blocks, "<SOURCE>")
        rows = self._run(sql.replace("<SOURCE>", "projects/aoc.md"), limit=5)
        self.assertEqual(rows, [{"tag": "p/aoc", "n": 2}])

        # Quotes in a path are escaped by doubling, as the skill instructs.
        rows = self._run(sql.replace("<SOURCE>", "O''Brien.md"), limit=5)
        self.assertEqual(rows, [{"tag": "p/aoc", "n": 1}])

    def test_existence_check(self):
        sql = _find(self.blocks, "<TAG>")
        self.assertEqual(self._run(sql.replace("<TAG>", "p/travel"), limit=1), [{"tag": "p/travel", "n": 1}])
        self.assertEqual(self._run(sql.replace("<TAG>", "p/invented"), limit=1), [])


if __name__ == "__main__":
    unittest.main()
