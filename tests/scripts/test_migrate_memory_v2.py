"""The one-off Memory v2 migration: export bullets, apply a reviewed classification."""
import datetime
import json
import os
import tempfile
import unittest

from core.knowledge.memory import store
from core.knowledge.memory.entries import FEEDBACK, PRIVATE
from scripts import migrate_memory_v2 as mig

TODAY = datetime.date(2026, 9, 24)


class MigrationCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pkm = tmp.name

    def write(self, rel, text):
        store.write_atomic(os.path.join(self.pkm, rel), text)

    def exists(self, rel):
        return os.path.exists(os.path.join(self.pkm, rel))


class TestExport(MigrationCase):

    def test_one_item_per_bullet_with_its_own_date(self):
        self.write("agents/a/MEMORY.md", "# Memory\n\n- Plain fact\n- Dated fact (Ref: 2026-05-01, 2026-06-02)\n")
        self.write("agents/a/FEEDBACK.md", "* [2026-04-11] Prefixed rule\nnot a bullet\n")
        self.write("agents/b/CONTEXT.md", "- Context fact\n")
        os.makedirs(os.path.join(self.pkm, "agents", "archive", "old"))

        items = mig.export(self.pkm)

        mtime = datetime.date.fromtimestamp(os.path.getmtime(os.path.join(self.pkm, "agents/a/MEMORY.md")))
        self.assertEqual(items, [
            {"agent": "a", "file": "MEMORY.md", "text": "Plain fact", "date": mtime.isoformat(), "tag": None},
            {"agent": "a", "file": "MEMORY.md", "text": "Dated fact", "date": "2026-06-02", "tag": None},
            {"agent": "a", "file": "FEEDBACK.md", "text": "Prefixed rule", "date": "2026-04-11", "tag": None},
            {"agent": "b", "file": "CONTEXT.md", "text": "Context fact",
             "date": items[3]["date"], "tag": None},
        ])

    def test_bullet_date_ignores_invalid_dates(self):
        fallback = datetime.date(2026, 1, 1)
        self.assertEqual(mig.bullet_date("x (Ref: 2026-13-40)", fallback), (fallback, "x (Ref: 2026-13-40)"))


class TestApply(MigrationCase):

    def items(self):
        return [
            {"agent": "a", "file": "CONTEXT.md", "text": "Lives in San Jose.", "date": "2026-09-01", "tag": "profile"},
            {"agent": "b", "file": "CONTEXT.md", "text": "lives in san jose", "date": "2026-09-10", "tag": "profile"},
            {"agent": "a", "file": "CONTEXT.md", "text": "Parents visiting.", "date": "2026-09-01", "tag": "family",
             "until": "2026-12-05"},
            {"agent": "a", "file": "MEMORY.md", "text": "Precedent.", "date": "2026-08-01", "tag": "private"},
            {"agent": "a", "file": "FEEDBACK.md", "text": "Be brief.", "date": "2026-08-01", "tag": "feedback"},
            {"agent": "a", "file": "MEMORY.md", "text": "Noise.", "date": "2026-08-01", "drop": True},
        ]

    def test_plan_merges_exact_duplicates(self):
        files, problems = mig.plan(self.items(), ["family"], self.pkm)
        self.assertEqual(problems, [])
        _, profile = files[store.profile_scope(self.pkm).path]
        [entry] = profile.entries
        self.assertEqual((entry.count, entry.first.isoformat(), entry.seen.isoformat()), (2, "2026-09-01", "2026-09-10"))

    def test_problems_block_the_whole_apply(self):
        self.write("agents/a/CONTEXT.md", "- old")
        items = self.items() + [{"agent": "a", "file": "MEMORY.md", "text": "?", "date": "2026-08-01", "tag": None},
                                {"agent": "a", "file": "MEMORY.md", "text": "?", "date": "2026-08-01", "tag": "pets"}]
        problems, report = mig.apply(items, self.pkm, today=TODAY)
        self.assertEqual(len(problems), 2)
        self.assertEqual(report, [])
        self.assertTrue(self.exists("agents/a/CONTEXT.md"))
        self.assertFalse(self.exists("wiki/memory"))

    def test_dry_run_writes_nothing(self):
        self.write("agents/a/CONTEXT.md", "- old")
        problems, report = mig.apply(self.items(), self.pkm, dry_run=True, today=TODAY)
        self.assertEqual(problems, [])
        self.assertIn("removed agents/a/CONTEXT.md", report)
        self.assertTrue(self.exists("agents/a/CONTEXT.md"))
        self.assertFalse(self.exists("wiki/memory"))

    def test_apply_writes_every_scope_and_retires_context(self):
        self.write("agents/a/CONTEXT.md", "- old")
        self.write("agents/a/MEMORY.md", "- old memory")
        self.write("agents/b/MEMORY.md", "- b's old memory, classified away")

        problems, report = mig.apply(self.items(), self.pkm, today=TODAY)

        self.assertEqual(problems, [])
        self.assertIn("wrote wiki/memory/TAGS.md", report)
        self.assertEqual(store.tag_names(self.pkm)[0], "food")
        self.assertEqual([e.text for e in store.load(store.profile_scope(self.pkm)).entries], ["Lives in San Jose."])
        [family] = store.load(store.topic_scope("family", self.pkm)).entries
        self.assertEqual(family.until, datetime.date(2026, 12, 5))
        self.assertEqual([e.text for e in store.load(store.private_scope("a", PRIVATE, self.pkm)).entries], ["Precedent."])
        self.assertEqual([e.text for e in store.load(store.private_scope("a", FEEDBACK, self.pkm)).entries], ["Be brief."])
        self.assertEqual(store.load(store.private_scope("b", PRIVATE, self.pkm)).entries, [])
        self.assertFalse(self.exists("agents/a/CONTEXT.md"))

    def test_existing_tags_file_is_kept(self):
        self.write("wiki/memory/TAGS.md", "## family\nKin.\n")
        problems, report = mig.apply(self.items(), self.pkm, today=TODAY)
        self.assertEqual(problems, [])
        self.assertNotIn("wrote wiki/memory/TAGS.md", report)
        self.assertEqual(store.tag_names(self.pkm), ["family"])

    def test_default_tags_parse(self):
        from core.knowledge.memory.entries import parse_tags
        self.assertEqual(len(parse_tags(mig.DEFAULT_TAGS)), 9)


class TestMain(MigrationCase):

    def test_export_then_dry_run(self):
        self.write("agents/a/MEMORY.md", "- fact\n")
        out = os.path.join(self.pkm, "export.json")
        self.assertEqual(mig.main(["--pkm-dir", self.pkm, "export", out]), 0)
        with open(out) as f:
            items = json.load(f)
        items[0]["tag"] = "private"
        with open(out, "w") as f:
            json.dump(items, f)
        self.assertEqual(mig.main(["--pkm-dir", self.pkm, "apply", out, "--dry-run"]), 0)

        items[0]["tag"] = None
        with open(out, "w") as f:
            json.dump(items, f)
        self.assertEqual(mig.main(["--pkm-dir", self.pkm, "apply", out]), 1)


if __name__ == "__main__":
    unittest.main()
