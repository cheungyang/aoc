"""Memory v2 storage: paths, budgets, eviction to the archive, tags."""
import datetime
import os
import tempfile
import unittest

from core.knowledge.memory import store
from core.knowledge.memory.entries import FEEDBACK, PRIVATE, PROFILE, Entry, MemoryFile, TagsError

D = datetime.date
TODAY = D(2026, 9, 24)


class VaultCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pkm = tmp.name

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()


class TestScopes(VaultCase):

    def test_paths(self):
        mem = os.path.join(self.pkm, "wiki", "memory")
        self.assertEqual(store.profile_scope(self.pkm).path, os.path.join(mem, "PROFILE.md"))
        self.assertEqual(store.profile_scope(self.pkm).archive, os.path.join(mem, "archive", "PROFILE.md"))
        self.assertEqual(store.topic_scope("food", self.pkm).path, os.path.join(mem, "topics", "food.md"))
        self.assertEqual(store.topic_scope("food", self.pkm).archive, os.path.join(mem, "archive", "food.md"))
        private = store.private_scope("day-planner", FEEDBACK, self.pkm)
        agent = os.path.join(self.pkm, "agents", "day-planner")
        self.assertEqual(private.path, os.path.join(agent, "FEEDBACK.md"))
        self.assertEqual(private.archive, os.path.join(agent, "archive", "FEEDBACK.md"))
        self.assertFalse(private.shared)
        self.assertTrue(store.topic_scope("food", self.pkm).shared)

    def test_scope_for_routes_by_tag(self):
        self.assertEqual(store.scope_for(PROFILE, "a", self.pkm).tag, PROFILE)
        self.assertEqual(store.scope_for(PRIVATE, "a", self.pkm).path,
                         os.path.join(self.pkm, "agents", "a", "MEMORY.md"))
        self.assertEqual(store.scope_for("food", "a", self.pkm).budget, store.BUDGET_TOPIC)


class TestBudget(VaultCase):

    def entry(self, text, seen=TODAY, count=1, tag="food", until=None):
        return Entry(tag, text, "a", seen, seen, count, until)

    def test_evicts_the_weakest_until_it_fits(self):
        old = self.entry("o" * 40, seen=TODAY - datetime.timedelta(days=200))
        popular = self.entry("p" * 40, seen=TODAY - datetime.timedelta(days=200), count=9)
        fresh = self.entry("f" * 40)
        kept, evicted, expired = store.enforce(MemoryFile([old, popular, fresh]), 90, TODAY)
        self.assertEqual(evicted, [old])
        self.assertEqual(kept.entries, [popular, fresh])
        self.assertEqual(expired, [])

    def test_feedback_outranks_equal_memory(self):
        memory = self.entry("m" * 40, tag=PRIVATE)
        feedback = self.entry("f" * 40, tag=FEEDBACK)
        self.assertGreater(store.score(feedback, TODAY), store.score(memory, TODAY))

    def test_expired_entries_leave_first(self):
        gone = self.entry("trip", until=TODAY - datetime.timedelta(days=1))
        kept, evicted, expired = store.enforce(MemoryFile([gone]), 1000, TODAY)
        self.assertEqual((kept.entries, evicted, expired), ([], [], [gone]))

    def test_unparsed_lines_are_never_evicted(self):
        memory = MemoryFile([self.entry("x" * 50)], unparsed=["- hand edit " + "y" * 50])
        kept, evicted, _ = store.enforce(memory, 10, TODAY)
        self.assertEqual(kept.unparsed, memory.unparsed)
        self.assertEqual(len(evicted), 1)

    def test_commit_archives_instead_of_deleting(self):
        scope = store.topic_scope("food", self.pkm)
        keep = self.entry("k" * 30, count=5)
        drop = self.entry("d" * 30, seen=TODAY - datetime.timedelta(days=100))
        retired = self.entry("r")
        counts = store.commit(scope, MemoryFile([keep, drop]), TODAY, {"retired: moved": [retired]})
        self.assertEqual(counts, {"retired: moved": 1})
        self.assertEqual(store.load(scope).entries, [keep, drop])

        small = store.Scope("food", scope.path, scope.archive, 40, "Topic: food")
        counts = store.commit(small, store.load(scope), TODAY)
        self.assertEqual(counts, {"budget": 1})
        self.assertEqual(store.load(scope).entries, [keep])
        archive = self.read(scope.archive)
        self.assertTrue(archive.startswith("# Archive: Topic: food\n"))
        self.assertIn("## 2026-09-24 · retired: moved\n" + retired.format(), archive)
        self.assertIn("## 2026-09-24 · budget\n" + drop.format(), archive)

    def test_save_writes_a_titled_file(self):
        scope = store.profile_scope(self.pkm)
        store.save(scope, MemoryFile([self.entry("Lives in San Jose.", tag=PROFILE)]))
        self.assertTrue(self.read(scope.path).startswith("# Profile\n\n- [profile] Lives in San Jose."))


class TestTagsAndFiles(VaultCase):

    def test_missing_tags_file_means_no_topics(self):
        self.assertEqual(store.load_tags(self.pkm), [])

    def test_tags_are_read_from_the_vault(self):
        store.write_atomic(store.tags_path(self.pkm), "## food\nDiet.\n")
        self.assertEqual(store.load_tags(self.pkm), [("food", "Diet.")])
        self.assertEqual(store.tag_names(self.pkm), ["food"])

    def test_malformed_tags_raise_but_tag_names_degrades(self):
        store.write_atomic(store.tags_path(self.pkm), "## Food\nDiet.\n")
        with self.assertRaises(TagsError):
            store.load_tags(self.pkm)
        self.assertEqual(store.tag_names(self.pkm), [])

    def test_read_missing_file_is_empty(self):
        self.assertEqual(store.read_text(os.path.join(self.pkm, "nope.md")), "")

    def test_write_atomic_leaves_no_temp_files(self):
        path = os.path.join(self.pkm, "a", "b.md")
        store.write_atomic(path, "x")
        self.assertEqual(os.listdir(os.path.dirname(path)), ["b.md"])


if __name__ == "__main__":
    unittest.main()
