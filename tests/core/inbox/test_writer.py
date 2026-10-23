"""Writing the channel inbox: pushes, replies, read signals. Nothing raises."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.inbox import writer
from core.inbox.model import LATE_REPLY_S, InboxRow, Priority, Source, Status, Via
from core.storage.channel_inbox import InboxStore
from tests.core.inbox import loader


def push(channel="day-planning", text="Good morning! How's your energy, one to ten?", **kw):
    kw.setdefault("schedule_id", "day-planner:morning-plan")
    kw.setdefault("agent_id", "day-planner")
    kw.setdefault("loader", loader())
    return writer.record_push(channel, text, **kw)


class TestPushes(unittest.TestCase):

    def test_a_push_puts_its_channel_in_the_inbox_with_its_speech_version(self):
        row = push(priority="DEFAULT", label="your morning check-in",
                   speech="Daisy here. How's your energy, one to ten?", question="How's your energy, one to ten?")
        stored = InboxStore().get("day-planning")
        self.assertEqual(stored, row)
        self.assertEqual((stored.source, stored.status, stored.priority), (Source.SCHEDULE, Status.UNREAD, Priority.DEFAULT))
        self.assertEqual(stored.agent_id, "day-planner", "the row is whoever answers in the channel")
        self.assertEqual(stored.label, "your morning check-in")
        self.assertEqual(stored.question, "How's your energy, one to ten?")
        self.assertIn("How's your energy", stored.excerpt)

    def test_the_row_belongs_to_whoever_answers_there_not_whoever_posted(self):
        """script-executor posts in #software-dev, which the concierge answers."""
        row = push("software-dev", "Coding tick: 2 tasks done.", schedule_id="script-executor:coding-tick",
                   agent_id="script-executor", priority="LOW", label="a coding update")
        self.assertEqual(row.agent_id, "main")
        self.assertIsNone(row.speech, "written once you join voice")

    def test_ignore_or_nothing_to_say_makes_no_row(self):
        self.assertIsNone(push(priority="IGNORE"))
        self.assertIsNone(push(text="   "))
        thread = SimpleNamespace(name="t", parent=SimpleNamespace(name="day-planning"), type="public_thread")
        with patch("core.runtime.execution_context._is_thread", return_value=True):
            self.assertIsNone(push(channel=thread))
        self.assertEqual(InboxStore().rows(), [])

    def test_a_question_needs_a_speech_version_to_belong_to(self):
        self.assertIsNone(push(question="Orphan?").question)

    def test_a_missing_label_has_a_spoken_default(self):
        self.assertEqual(push().label, writer.DEFAULT_LABEL)

    def test_a_failing_store_never_breaks_the_run(self):
        with patch.object(InboxStore, "put", side_effect=RuntimeError("disk full")):
            self.assertIsNone(push())


class TestReplies(unittest.TestCase):

    def test_a_reply_is_a_candidate_until_judged(self):
        now = time.time()
        row = writer.record_reply("topic-research", "Here's more on the topic. History or math first?",
                                  asked_at=now - 60, loader=loader(), now=now)
        self.assertEqual((row.source, row.status, row.agent_id), (Source.REPLY, Status.UNREAD, "topic-researcher"))
        self.assertTrue(row.is_candidate)
        self.assertEqual(row.label, writer.REPLY_LABEL)

    def test_a_reply_ten_minutes_after_your_message_is_late(self):
        now = time.time()
        row = writer.record_reply("topic-research", "Done researching.", asked_at=now - LATE_REPLY_S, loader=loader(), now=now)
        self.assertIs(row.source, Source.LATE_REPLY)
        self.assertFalse(row.is_candidate)
        self.assertEqual(row.label, writer.LATE_REPLY_LABEL)

    def test_a_reply_replaces_the_channels_older_row(self):
        push()
        writer.record_reply("day-planning", "Noted.", asked_at=None, loader=loader())
        self.assertIs(InboxStore().get("day-planning").source, Source.REPLY)


class TestReadSignals(unittest.TestCase):

    def test_posting_in_the_channel_finishes_its_row_and_drops_a_candidate(self):
        push()
        self.assertTrue(writer.mark_read("day-planning", Via.TYPED))
        stored = InboxStore().get("day-planning")
        self.assertEqual((stored.status, stored.status_via), (Status.DONE, Via.TYPED))
        self.assertFalse(writer.mark_read("day-planning", Via.TYPED), "nothing left to finish")

        writer.record_reply("day-planning", "Sure.", asked_at=None, loader=loader())
        writer.mark_read("day-planning", Via.VOICE)
        self.assertIs(InboxStore().get("day-planning").status, Status.DONE)

    def test_a_check_mark_on_an_older_post_changes_nothing(self):
        now = time.time()
        push(now=now)
        long_before = now - writer.REACTION_WINDOW_S - 60
        self.assertFalse(writer.mark_read("day-planning", Via.REACTION, message_time=long_before))
        self.assertTrue(writer.mark_read("day-planning", Via.REACTION, message_time=now - 30))
        self.assertIs(InboxStore().get("day-planning").status_via, Via.REACTION)

    def test_hearing_a_row_with_a_question_waits_for_your_answer(self):
        row = push(speech="Daisy here. How's your energy?", question="How's your energy?")
        heard = writer.mark_heard(row)
        self.assertIs(heard.status, Status.AWAITING_REPLY)
        self.assertIs(InboxStore().get("day-planning").status, Status.AWAITING_REPLY)

    def test_hearing_a_row_without_one_finishes_it(self):
        row = push("topic-research", "Your new syllabus is ready.", speech="Ted here. Your syllabus is ready.")
        self.assertIs(writer.mark_heard(row).status, Status.DONE)

    def test_hearing_a_replaced_post_leaves_the_newer_one_alone(self):
        old = push(now=100.0, speech="Old.")
        push(now=200.0, speech="New.")
        self.assertIsNone(writer.mark_heard(old))
        self.assertIs(InboxStore().get("day-planning").status, Status.UNREAD)


class TestSpeechResults(unittest.TestCase):

    def _candidate(self):
        return writer.record_reply("pkm-wiki", "Should the two notes on habits be merged?", asked_at=None,
                                   agent_id="wiki-gardener", loader=loader())

    def test_a_candidate_that_asks_you_something_stays(self):
        stored = writer.store_speech(self._candidate(), "Should the two notes be merged?", "Should the two notes be merged?")
        self.assertEqual((stored.status, stored.question), (Status.UNREAD, "Should the two notes be merged?"))
        self.assertFalse(stored.is_candidate)

    def test_any_other_candidate_is_dropped(self):
        stored = writer.store_speech(self._candidate(), "I merged them.", None)
        self.assertIs(stored.status, Status.DONE)
        self.assertIs(InboxStore().get("pkm-wiki").status, Status.DONE)

    def test_a_result_for_a_replaced_post_is_discarded(self):
        candidate = self._candidate()
        InboxStore().put(InboxRow(channel="pkm-wiki", agent_id="wiki-gardener", posted_at=candidate.posted_at + 5,
                                  source=Source.SCHEDULE, label="new inbox articles"))
        self.assertIsNone(writer.store_speech(candidate, "Merge?", "Merge?"))


class TestListeners(unittest.TestCase):

    def test_new_rows_reach_subscribers_until_they_unsubscribe(self):
        seen = []
        unsubscribe = writer.subscribe(seen.append)
        try:
            row = push()
        finally:
            unsubscribe()
        push("topic-research", "Later.")
        self.assertEqual(seen, [row])

    def test_a_failing_listener_breaks_nothing(self):
        unsubscribe = writer.subscribe(lambda row: 1 / 0)
        try:
            self.assertIsNotNone(push())
        finally:
            unsubscribe()


if __name__ == "__main__":
    unittest.main()
