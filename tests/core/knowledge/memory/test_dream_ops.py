"""The dream proposes, code disposes: parsing replies and applying ops."""
import datetime
import tempfile
import unittest

from core.knowledge.memory import dream_ops, store
from core.knowledge.memory import state as mstate
from core.knowledge.memory.entries import FEEDBACK, PRIVATE, PROFILE, Entry, MemoryFile

D = datetime.date
TODAY = D(2026, 9, 24)
TAGS = [("food", "Diet."), ("travel", "Trips.")]
TAG_NAMES = ["food", "travel"]


def reply(ops, status="Dreamed", learnings="learned"):
    return (f"<dream_response><status>{status}</status><ops>{ops}</ops>"
            f"<errors>None</errors><learnings>{learnings}</learnings></dream_response>")


class TestParseReply(unittest.TestCase):

    def test_parses_every_op(self):
        r = dream_ops.parse_reply(reply(
            '<add tag="Food" until="2026-12-05">Likes &amp; tea</add>'
            '<confirm id="e1"/><update id="e2">new</update>'
            '<retire id="e3" reason="trip ended"/><merge ids="e4 e5">both</merge>'
        ))
        self.assertIsNone(r.error)
        self.assertEqual(sorted(o.kind for o in r.ops), ["add", "confirm", "merge", "retire", "update"])
        kinds = {o.kind: o for o in r.ops}
        self.assertEqual((kinds["add"].tag, kinds["add"].text, kinds["add"].until), ("food", "Likes & tea", "2026-12-05"))
        self.assertEqual(kinds["confirm"].id, "e1")
        self.assertEqual(kinds["update"].text, "new")
        self.assertEqual(kinds["retire"].reason, "trip ended")
        self.assertEqual(kinds["merge"].ids, ("e4", "e5"))
        self.assertEqual(r.learnings, "learned")

    def test_quiet_night(self):
        r = dream_ops.parse_reply("<dream_response><status>No new memories</status><errors>None</errors></dream_response>")
        self.assertEqual((r.status, r.ops, r.error), (dream_ops.STATUS_EMPTY, [], None))

    def test_empty_ops_element(self):
        r = dream_ops.parse_reply("<dream_response><status>Dreamed</status><ops/><errors>None</errors></dream_response>")
        self.assertEqual((r.status, r.ops, r.error), (dream_ops.STATUS_DREAMED, [], None))

    def test_structural_failures(self):
        cases = {
            "": "empty response",
            "Sure! Happy to help.": "no <dream_response>",
            reply("", status="[Dreamed or No new memories]"): "unrecognised status",
            "<dream_response><status>Dreamed</status><errors>None</errors></dream_response>": "without <ops>",
            "<dream_response><status>Dreamed</status><errors>disk full</errors></dream_response>": "disk full",
        }
        for raw, expected in cases.items():
            self.assertIn(expected, dream_ops.parse_reply(raw).error, raw)


class OpsCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.pkm = tmp.name
        self.state = mstate.load(self.pkm)

    def put(self, scope, *entries):
        store.save(scope, MemoryFile(list(entries)))

    def entry(self, tag, text, seen=TODAY, count=1, until=None, src="a"):
        return Entry(tag, text, src, seen, seen, count, until)

    def dream(self, ops, agent="a", subs=("food",), logs=None, compaction=False, tags=None):
        if compaction:
            dream_input = dream_ops.build_compaction_input(tags or ["food"], self.state["near_duplicates"], TODAY, self.pkm)
        else:
            dream_input = dream_ops.build_input(agent, TAGS, logs or {}, TODAY, self.pkm)
        parsed = dream_ops.parse_reply(reply(ops))
        self.assertIsNone(parsed.error)
        report = dream_ops.apply_reply(agent, parsed, dream_input, TAG_NAMES, list(subs), TODAY, self.state,
                                       self.pkm, compaction=compaction)
        return dream_input, report

    def texts(self, scope):
        return [e.text for e in store.load(scope).entries]

    def events(self, kind):
        return [(e["agent"], e["tag"]) for e in self.state["events"] if e["kind"] == kind]


class TestBuildInput(OpsCase):

    def test_numbers_every_scope_and_marks_stale_for_review(self):
        old = TODAY - datetime.timedelta(days=120)
        self.put(store.profile_scope(self.pkm), self.entry(PROFILE, "Lives in <San Jose>"))
        self.put(store.topic_scope("travel", self.pkm), self.entry("travel", "Old trip", seen=old))
        self.put(store.private_scope("a", FEEDBACK, self.pkm), self.entry(FEEDBACK, "Be brief"))
        self.put(store.private_scope("b", PRIVATE, self.pkm), self.entry(PRIVATE, "Someone else's"))

        dream_input = dream_ops.build_input("a", TAGS, {"2026-09-23.md": "log body"}, TODAY, self.pkm)

        self.assertEqual(sorted(dream_input.ids), ["e1", "e2", "e3"])
        self.assertEqual(dream_input.reviewed, {"e2"})
        self.assertIn('<entry id="e1" tag="profile">Lives in &lt;San Jose&gt;</entry>', dream_input.xml)
        self.assertIn('<entry id="e2" tag="travel" stale="true">Old trip</entry>', dream_input.xml)
        self.assertNotIn("Someone else", dream_input.xml)
        self.assertIn('<tag name="food">Diet.</tag>', dream_input.xml)
        self.assertIn('<tag name="profile">', dream_input.xml)
        self.assertIn("log body", dream_input.xml)

    def test_review_is_capped_oldest_first(self):
        entries = [self.entry("food", f"f{i}", seen=TODAY - datetime.timedelta(days=100 + i)) for i in range(7)]
        self.put(store.topic_scope("food", self.pkm), *entries)
        dream_input = dream_ops.build_input("a", TAGS, {}, TODAY, self.pkm)
        reviewed = {dream_input.ids[i][1].text for i in dream_input.reviewed}
        self.assertEqual(reviewed, {"f6", "f5", "f4", "f3", "f2"})

    def test_expired_entries_are_not_shown(self):
        self.put(store.topic_scope("food", self.pkm), self.entry("food", "gone", until=TODAY - datetime.timedelta(days=1)))
        self.assertEqual(dream_ops.build_input("a", TAGS, {}, TODAY, self.pkm).ids, {})


class TestApply(OpsCase):

    def test_add_routes_by_tag(self):
        _, report = self.dream('<add tag="food">Oat milk OK.</add><add tag="private">A precedent.</add>'
                               '<add tag="feedback">Be brief.</add><add tag="travel" until="2026-12-05">Trip.</add>')
        self.assertEqual(report.applied, 4)
        self.assertEqual(self.texts(store.topic_scope("food", self.pkm)), ["Oat milk OK."])
        self.assertEqual(self.texts(store.private_scope("a", PRIVATE, self.pkm)), ["A precedent."])
        self.assertEqual(self.texts(store.private_scope("a", FEEDBACK, self.pkm)), ["Be brief."])
        trip = store.load(store.topic_scope("travel", self.pkm)).entries[0]
        self.assertEqual((trip.until, trip.src), (D(2026, 12, 5), "a"))
        self.assertEqual(self.events(mstate.UNSUBSCRIBED_WRITE), [("a", "travel")])
        self.assertEqual(report.changed_topics, {"food", "travel"})

    def test_unknown_tag_goes_private_and_is_counted(self):
        self.dream('<add tag="pets">Nik eats kibble.</add>')
        self.assertEqual(self.texts(store.private_scope("a", PRIVATE, self.pkm)), ["Nik eats kibble."])
        self.assertEqual(self.events(mstate.UNKNOWN_TAG), [("a", "pets")])

    def test_placeholder_text_is_rejected(self):
        _, report = self.dream('<add tag="food">[one-sentence fact]</add><add tag="food"> </add>')
        self.assertEqual(report.applied, 0)
        self.assertEqual(len(report.rejected), 2)

    def test_profile_writes_are_capped(self):
        _, report = self.dream('<add tag="profile">One.</add><add tag="profile">Two.</add><add tag="profile">Three.</add>')
        self.assertEqual(self.texts(store.profile_scope(self.pkm)), ["One.", "Two."])
        self.assertEqual(self.texts(store.private_scope("a", PRIVATE, self.pkm)), ["Three."])
        self.assertEqual(report.profile_changes, ["PROFILE + One.", "PROFILE + Two."])
        self.assertEqual(self.events(mstate.PROFILE_CAP), [("a", PROFILE)])

    def test_exact_duplicate_confirms_instead_of_adding(self):
        self.put(store.topic_scope("food", self.pkm), self.entry("food", "Oat milk OK.", seen=D(2026, 9, 1), src="b"))
        self.dream('<add tag="food">oat milk ok</add>')
        [entry] = store.load(store.topic_scope("food", self.pkm)).entries
        self.assertEqual((entry.count, entry.seen, entry.src), (2, TODAY, "b"))

    def test_near_duplicate_is_added_and_flagged(self):
        self.put(store.topic_scope("food", self.pkm), self.entry("food", "Prefers oat milk in coffee."))
        self.dream('<add tag="food">Prefers oat milk in his coffee.</add>')
        self.assertEqual(len(store.load(store.topic_scope("food", self.pkm)).entries), 2)
        self.assertEqual(self.state["near_duplicates"][0]["tag"], "food")
        self.assertTrue(dream_ops.needs_compaction("food", self.state, self.pkm))

    def test_confirm_update_retire(self):
        scope = store.topic_scope("food", self.pkm)
        self.put(scope, self.entry("food", "A", seen=D(2026, 9, 1)), self.entry("food", "B"), self.entry("food", "C"))
        _, report = self.dream('<confirm id="e1"/><update id="e2">B2</update><retire id="e3" reason="moved"/>')
        entries = store.load(scope).entries
        self.assertEqual([(e.text, e.count, e.seen) for e in entries], [("A", 2, TODAY), ("B2", 2, TODAY)])
        self.assertEqual(report.archived, {"retired": 1})
        with open(scope.archive) as f:
            self.assertIn("retired: moved", f.read())

    def test_profile_update_and_retire_are_reported(self):
        self.put(store.profile_scope(self.pkm), self.entry(PROFILE, "Old"), self.entry(PROFILE, "Gone"))
        _, report = self.dream('<update id="e1">New</update><retire id="e2" reason="x"/>')
        self.assertEqual(report.profile_changes, ["PROFILE ~ New", "PROFILE − Gone"])

    def test_unknown_ids_and_merge_outside_compaction_are_rejected(self):
        _, report = self.dream('<confirm id="e9"/><merge ids="e1 e2">x</merge>')
        self.assertEqual(report.applied, 0)
        self.assertEqual(len(report.rejected), 2)

    def test_silence_means_keep_for_stale_entries(self):
        old = TODAY - datetime.timedelta(days=120)
        scope = store.topic_scope("travel", self.pkm)
        self.put(scope, self.entry("travel", "Old", seen=old))
        self.dream("")
        self.assertEqual(store.load(scope).entries[0].seen, TODAY)

    def test_an_entry_changed_by_another_dream_is_left_alone(self):
        scope = store.topic_scope("food", self.pkm)
        self.put(scope, self.entry("food", "A"))
        dream_input = dream_ops.build_input("a", TAGS, {}, TODAY, self.pkm)
        self.put(scope, self.entry("food", "A edited"))
        parsed = dream_ops.parse_reply(reply('<retire id="e1" reason="x"/>'))
        report = dream_ops.apply_reply("a", parsed, dream_input, TAG_NAMES, ["food"], TODAY, self.state, self.pkm)
        self.assertIn("changed underneath", report.rejected[0])
        self.assertEqual(self.texts(scope), ["A edited"])

    def test_budget_overflow_is_archived_and_reported(self):
        scope = store.topic_scope("food", self.pkm)
        self.put(scope, self.entry("food", "x" * 990, seen=D(2026, 1, 1)))
        _, report = self.dream('<add tag="food">A new fact.</add>')
        self.assertEqual(self.texts(scope), ["A new fact."])
        self.assertEqual(report.archived, {"evicted": 1})
        self.assertIn("evicted 1", report.summary())


class TestCompaction(OpsCase):

    def test_merge_combines_entries(self):
        scope = store.topic_scope("food", self.pkm)
        self.put(scope, self.entry("food", "Oat milk.", seen=D(2026, 9, 1), count=2),
                 self.entry("food", "Oat milk in coffee.", count=3, until=D(2026, 12, 1)))
        mstate.flag_near_duplicate(self.state, "food", "Oat milk.", "Oat milk in coffee.")
        dream_input, report = self.dream('<merge ids="e1 e2">Oat milk, including in coffee.</merge><add tag="food">x</add>',
                                         agent="wiki-gardener", compaction=True)
        self.assertIn('<near_duplicate ids="e2 e1"/>', dream_input.xml)  # texts are stored sorted
        self.assertIn(f'budget="{store.BUDGET_TOPIC}"', dream_input.xml)
        [merged] = store.load(scope).entries
        self.assertEqual((merged.text, merged.count, merged.first, merged.until),
                         ("Oat milk, including in coffee.", 5, D(2026, 9, 1), D(2026, 12, 1)))
        self.assertEqual(report.rejected, ["add food: not allowed in compaction"])
        dream_ops.clear_flags(self.state, ["food"])
        self.assertEqual(self.state["near_duplicates"], [])

    def test_merge_across_files_is_rejected(self):
        self.put(store.topic_scope("food", self.pkm), self.entry("food", "A"))
        self.put(store.topic_scope("travel", self.pkm), self.entry("travel", "B"))
        _, report = self.dream('<merge ids="e1 e2">AB</merge>', compaction=True, tags=["food", "travel"])
        self.assertEqual(report.applied, 0)

    def test_needs_compaction_when_nearly_full(self):
        self.put(store.topic_scope("food", self.pkm), self.entry("food", "x" * 900))
        self.assertTrue(dream_ops.needs_compaction("food", self.state, self.pkm))
        self.assertFalse(dream_ops.needs_compaction("travel", self.state, self.pkm))


if __name__ == "__main__":
    unittest.main()
