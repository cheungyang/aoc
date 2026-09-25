"""Memory v2 in the prompt: subscriptions filtered by TAGS.md, metadata hidden."""
import datetime
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from core.knowledge.memory import inject, store
from core.knowledge.memory.entries import FEEDBACK, PRIVATE, MemoryFile, new_entry

TODAY = datetime.date(2026, 9, 24)


class TestInject(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pkm = tmp.name
        store.write_atomic(store.tags_path(self.pkm), "## food\nDiet.\n## travel\nTrips.\n")

    def put(self, scope, *texts):
        store.save(scope, MemoryFile([new_entry(scope.tag, t, "a", TODAY) for t in texts]))

    def test_memory_topics_keep_only_known_tags(self):
        config = {"memory_topics": ["travel", "food", "pets"]}
        self.assertEqual(inject.memory_topics(config, self.pkm), ["food", "travel"])
        self.assertEqual(inject.unknown_topics(config, self.pkm), ["pets"])
        self.assertEqual(inject.memory_topics({}, self.pkm), [])
        self.assertEqual(inject.unknown_topics({}, self.pkm), [])

    def test_unknown_topics_are_reported_not_fatal(self):
        out = StringIO()
        with redirect_stdout(out):
            inject.report_unknown_topics({"id": "a", "memory_topics": ["food", "pets"]}, self.pkm)
            inject.report_unknown_topics({"id": "b", "memory_topics": ["food"]}, self.pkm)
        self.assertIn("agent 'a' subscribes to memory topics not in TAGS.md: ['pets']", out.getvalue())
        self.assertNotIn("'b'", out.getvalue())

    def test_a_broken_vault_is_reported_not_raised(self):
        out = StringIO()
        with patch.object(inject.store, "tag_names", side_effect=OSError("unreadable")), redirect_stdout(out):
            inject.report_unknown_topics({"id": "a", "memory_topics": ["food"]}, self.pkm)
        self.assertIn("could not check memory_topics for a: unreadable", out.getvalue())

    def test_renders_facts_without_metadata(self):
        self.put(store.profile_scope(self.pkm), "Lives in San Jose.")
        self.put(store.topic_scope("food", self.pkm), "Oat milk OK.", "No cilantro.")
        self.put(store.private_scope("a", PRIVATE, self.pkm), "Precedent.")
        self.put(store.private_scope("a", FEEDBACK, self.pkm), "Be brief.")

        self.assertEqual(inject.render_profile(self.pkm), "- Lives in San Jose.")
        self.assertEqual(inject.render_topics(["food", "travel"], self.pkm), "### food\n- Oat milk OK.\n- No cilantro.")
        self.assertEqual(inject.render_memory("a", self.pkm), "- Precedent.")
        self.assertEqual(inject.render_feedback("a", self.pkm), "- Be brief.")

    def test_hand_edits_still_reach_the_prompt(self):
        store.write_atomic(store.profile_scope(self.pkm).path, "# Profile\n\n- typed by hand\n")
        self.assertEqual(inject.render_profile(self.pkm), "- typed by hand")

    def test_missing_files_render_empty(self):
        self.assertEqual(inject.render_profile(self.pkm), "")
        self.assertEqual(inject.render_topics(["food"], self.pkm), "")
        self.assertEqual(inject.render_memory("ghost", self.pkm), "")


if __name__ == "__main__":
    unittest.main()
