"""Voice briefings: the digest, "next agent", what an agent left you, and
live announcements -- checked against the drive in the plan (Part 3, 3.1) --
and the briefing as a voice room hook."""
import asyncio
import unittest
from unittest.mock import AsyncMock

from core.inbox.model import InboxRow, Priority, Source, Status
from core.inbox.speech_writer import SpeechWriter
from core.inbox.voice_briefing import (
    VoiceBriefing,
    announcement,
    compose_digest,
    entries_for,
    is_next_agent,
    nothing_left,
    ordered,
    should_announce,
)
from core.storage.channel_inbox import InboxStore
from core.voice.room.focus import FocusDirectory
from tests.core.inbox import loader

DIRECTORY = FocusDirectory.build(loader(), voice_host="main")
JOINED = 1_000_000.0  # 8:10, when you join
HOUR = 8


def row(channel, agent_id, minutes_before_join, **kw):
    base = dict(channel=channel, agent_id=agent_id, posted_at=JOINED - minutes_before_join * 60,
                source=Source.SCHEDULE)
    base.update(kw)
    return InboxRow(**base)


TED = row("topic-research", "topic-researcher", 14 * 60, source=Source.LATE_REPLY, label="a reply for you",
          speech="Ted here. I found more on the topic you asked about yesterday. "
                 "Do you want the history first, or the math?",
          question="Do you want the history first, or the math?")
DAISY = row("day-planning", "day-planner", 130, label="your morning check-in",
            speech="Daisy here. Yesterday you noted the afternoon slipped away. "
                   "How's your energy this morning, one to ten?",
            question="How's your energy this morning, one to ten?")
FRANKIE = row("fitness-coach", "fitness-coach", 60, priority=Priority.LOW, label="today's workout",
              speech="Frankie here. Today's workout: twenty minutes of intervals, then ten of stretching. "
                     "Want to go with it?")
WILLIAM = row("pkm-wiki", "wiki-gardener", 600, source=Source.REPLY, label="a question for you",
              excerpt="Should the two notes on habits be merged into one?")  # a candidate
MARVIN = row("market-scout", "market-scout", 30, label="the market scan", speech="Marvin here. Markets are flat.")
RIO = row("real-estate", "property-scout", 90, label="this week's listings", speech="Rio here. Three new listings.")


def entries(*rows):
    return entries_for(rows, DIRECTORY, now=JOINED)


class TestDigest(unittest.TestCase):

    def test_the_drive_in_the_plan(self):
        self.assertEqual(
            compose_digest(entries(TED, DAISY, FRANKIE, WILLIAM), HOUR),
            "Morning. Two agents have something for you. Ted and Daisy are waiting for your answer. "
            "Start with Ted? Say 'start with Ted' to begin.",
        )

    def test_labels_newest_first_after_the_questions(self):
        self.assertEqual(
            compose_digest(entries(DAISY, MARVIN, RIO), 14),
            "Afternoon. Three agents have something for you. Daisy is waiting for your answer. "
            "Marvin has the market scan, and Rio this week's listings. Start with Daisy? Say 'start with Daisy' to begin.",
        )

    def test_one_agent_and_no_questions(self):
        self.assertEqual(
            compose_digest(entries(MARVIN), 20),
            "Evening. One agent has something for you. Marvin has the market scan. "
            "Start with Marvin? Say 'start with Marvin' to begin.",
        )

    def test_nothing_in_it_means_no_digest(self):
        """LOW rows and unjudged replies aren't in the digest; expired and finished rows aren't rows."""
        old = row("market-scout", "market-scout", 25 * 60, label="the market scan", speech="Old.")
        done = row("real-estate", "property-scout", 5, status=Status.DONE, label="x", speech="Done.")
        self.assertIsNone(compose_digest(entries(FRANKIE, WILLIAM, old, done), HOUR))

    def test_a_heard_question_is_still_waiting(self):
        heard = row("day-planning", "day-planner", 130, status=Status.AWAITING_REPLY,
                    speech=DAISY.speech, question=DAISY.question)
        self.assertIn("Daisy is waiting for your answer.", compose_digest(entries(heard), HOUR))

    def test_a_long_digest_stays_short(self):
        many = [row(ch, aid, i, label=f"label {i}", speech="S.") for i, (ch, aid) in enumerate([
            ("market-scout", "market-scout"), ("real-estate", "property-scout"), ("day-planning", "day-planner"),
            ("topic-research", "topic-researcher"), ("pkm-wiki", "wiki-gardener"), ("software-dev", "main"),
        ])]
        digest = compose_digest(entries(*many), HOUR)
        self.assertIn("and 2 more.", digest)
        self.assertLess(len(digest.split()), 60, "about 20 seconds or less")

    def test_a_channel_the_concierge_answers_is_named_by_its_channel(self):
        tick = row("software-dev", "main", 5, label="a coding update", speech="Two tasks done.")
        self.assertEqual(
            compose_digest(entries(tick), HOUR),
            "Morning. One agent has something for you. The software dev channel has a coding update. "
            "Start with software dev? Say 'start with software dev' to begin.",
        )


class TestNextAgent(unittest.TestCase):

    def test_the_keyword_as_a_whole_utterance(self):
        for transcript in ("Next agent.", "next agent please", "Okay, next agent", "go to the next agent",
                           "take me to the next agent", "Move on to the next agent", "skip to next agent"):
            with self.subTest(transcript=transcript):
                self.assertTrue(is_next_agent(transcript))

    def test_not_inside_a_request(self):
        for transcript in ("Who is the next agent?", "next agent meeting is at noon", "next", "the next one",
                           "add a task for the next agent"):
            with self.subTest(transcript=transcript):
                self.assertFalse(is_next_agent(transcript))

    def test_order_questions_then_priority_oldest_first_low_last_then_awaiting(self):
        awaiting = row("real-estate", "property-scout", 300, status=Status.AWAITING_REPLY,
                       speech="Rio here. Tour on Saturday?", question="Tour on Saturday?")
        urgent = row("market-scout", "market-scout", 5, priority=Priority.HIGHEST, label="a big move", speech="S.")
        got = [e.focus.key for e in ordered(entries(FRANKIE, awaiting, DAISY, urgent, TED))]
        self.assertEqual(got, ["topic-research", "day-planning", "market-scout", "fitness-coach", "real-estate"])

    def test_never_the_channel_you_are_in_an_unjudged_reply_or_a_visited_question(self):
        heard_ted = row("topic-research", "topic-researcher", 60, status=Status.AWAITING_REPLY,
                        speech=TED.speech, question=TED.question)
        got = ordered(entries(DAISY, WILLIAM, heard_ted), visited={"topic-research"}, exclude="day-planning")
        self.assertEqual(got, [])

    def test_thats_everything_and_who_is_still_waiting(self):
        heard_ted = row("topic-research", "topic-researcher", 60, status=Status.AWAITING_REPLY,
                        speech=TED.speech, question=TED.question)
        self.assertEqual(nothing_left(entries(heard_ted)), "That's everything. Ted is still waiting for your answer.")
        self.assertEqual(nothing_left([]), "That's everything.")


class TestAnnouncements(unittest.TestCase):

    def test_what_gets_a_live_line(self):
        judged_william = row("pkm-wiki", "wiki-gardener", 600, source=Source.REPLY, label="a question for you",
                             speech="William here. Merge the notes?", question="Merge the notes?")
        late_ted = row("topic-research", "topic-researcher", 30, source=Source.LATE_REPLY, label="a reply for you",
                       speech="Ted here. Done.")
        new_push_question = row("day-planning", "day-planner", -2, label="x", speech="S?", question="S?")
        new_push_plain = row("market-scout", "market-scout", -2, label="the market scan", speech="S.")
        new_highest = row("real-estate", "property-scout", -2, priority=Priority.HIGHEST, label="a price drop", speech="S.")
        new_low_question = row("fitness-coach", "fitness-coach", -2, priority=Priority.LOW, label="x", speech="S?",
                               question="S?")
        old_push_question = DAISY

        cases = {judged_william: True, late_ted: True, new_push_question: True, new_highest: True,
                 new_push_plain: False, new_low_question: False, old_push_question: False, WILLIAM: False}
        for r, expected in cases.items():
            with self.subTest(channel=r.channel, source=r.source):
                self.assertIs(should_announce(r, JOINED, now=JOINED), expected)

    def test_the_line_tells_you_the_words(self):
        judged = row("pkm-wiki", "wiki-gardener", 600, source=Source.REPLY, speech="Merge?", question="Merge?")
        late = row("topic-research", "topic-researcher", 30, source=Source.LATE_REPLY, speech="Done.")
        highest = row("real-estate", "property-scout", -2, priority=Priority.HIGHEST, label="a price drop", speech="S.")
        self.assertEqual(announcement(entries(judged)[0]),
                         "William has a question for you. Say 'switch to William' to hear it.")
        self.assertEqual(announcement(entries(late)[0]), "Ted has a reply for you. Say 'switch to Ted' to hear it.")
        self.assertEqual(announcement(entries(highest)[0]), "Rio has a price drop. Say 'switch to Rio' to hear it.")


class TestVoiceBriefing(unittest.IsolatedAsyncioTestCase):
    """One drive's state, on the real store (a temporary memory.db)."""

    def setUp(self):
        self.store = InboxStore()
        self.written = []

        async def write(r, name):
            self.written.append((r.channel, name))
            return "William here. Should the two notes on habits be merged?", "Should the two notes on habits be merged?"

        self.briefing = VoiceBriefing(store=self.store, writer=SpeechWriter(write=write), clock=lambda: JOINED,
                                      hour=lambda: HOUR)
        self.announced = []

    def put(self, *rows):
        for r in rows:
            self.store.put(r)

    async def _settle(self):
        for _ in range(5):
            await asyncio.sleep(0)
        tasks = [task for _, task in self.briefing.writer._tasks.values()]
        await asyncio.gather(*tasks)

    async def test_arriving_plays_the_speech_version_then_replays_only_the_question(self):
        self.put(DAISY)
        self.briefing.begin()
        daisy = DIRECTORY.get("day-planning")

        text, r = await self.briefing.left_for_you(DIRECTORY, daisy)
        self.assertEqual(text, DAISY.speech)
        self.briefing.heard(r)
        self.assertIs(self.store.get("day-planning").status, Status.AWAITING_REPLY)

        text, _ = await self.briefing.left_for_you(DIRECTORY, daisy)
        self.assertEqual(text, "How's your energy this morning, one to ten?")

    async def test_a_row_without_a_question_is_finished_once_heard(self):
        self.put(MARVIN)
        self.briefing.begin()
        text, r = await self.briefing.left_for_you(DIRECTORY, DIRECTORY.get("market-scout"))
        self.briefing.heard(r)
        self.assertIs(self.store.get("market-scout").status, Status.DONE)
        self.assertIsNone(await self.briefing.left_for_you(DIRECTORY, DIRECTORY.get("market-scout")))

    async def test_arriving_waits_for_a_speech_version_still_being_written(self):
        self.put(WILLIAM)
        self.briefing.begin()
        text, r = await self.briefing.left_for_you(DIRECTORY, DIRECTORY.get("pkm-wiki"))
        self.assertEqual(text, "William here. Should the two notes on habits be merged?")
        self.assertEqual(self.written, [("pkm-wiki", "William")])
        self.assertEqual(self.announced, [], "you're hearing it: no announcement")

    async def test_next_agent_walks_the_drive(self):
        self.put(TED, DAISY, FRANKIE)
        self.briefing.begin()
        lobby = DIRECTORY.lobby

        target, _ = self.briefing.next_unfinished(DIRECTORY, lobby)
        self.assertEqual(target.key, "topic-research")
        _, ted_row = await self.briefing.left_for_you(DIRECTORY, target)
        self.briefing.heard(ted_row)  # unanswered: AWAITING_REPLY, visited

        target, _ = self.briefing.next_unfinished(DIRECTORY, target)
        self.assertEqual(target.key, "day-planning")
        self.store.set_status("day-planning", Status.DONE)  # you answered Daisy

        target, _ = self.briefing.next_unfinished(DIRECTORY, target)
        self.assertEqual(target.key, "fitness-coach", "LOW, reached last")
        _, frankie_row = await self.briefing.left_for_you(DIRECTORY, target)
        self.briefing.heard(frankie_row)

        target, said = self.briefing.next_unfinished(DIRECTORY, target)
        self.assertIsNone(target)
        self.assertEqual(said, "That's everything. Ted is still waiting for your answer.")

    async def test_joining_writes_what_has_no_speech_version_and_announces_what_needs_you(self):
        script_post = row("software-dev", "main", 30, label="a coding update", excerpt="Two tasks done.")
        self.put(WILLIAM, script_post, DAISY)
        self.briefing.begin()
        started = self.briefing.prepare(DIRECTORY, announce=self.announced.append)
        await self._settle()

        self.assertEqual(started, 2)
        self.assertEqual(sorted(self.written), [("pkm-wiki", "William"), ("software-dev", "the software dev channel")])
        self.assertEqual(self.announced, ["William has a question for you. Say 'switch to William' to hear it."])

    async def test_a_row_that_turns_up_while_you_are_in_voice_is_announced_once(self):
        self.briefing.begin()
        new = row("day-planning", "day-planner", -1, label="x", speech="Daisy here. Lunch at noon?",
                  question="Lunch at noon?")
        self.put(new)
        self.briefing.consider(new, DIRECTORY, announce=self.announced.append)
        self.briefing.consider(new, DIRECTORY, announce=self.announced.append)
        self.assertEqual(self.announced, ["Daisy has a question for you. Say 'switch to Daisy' to hear it."])

    async def test_nothing_is_announced_outside_a_drive(self):
        new = row("day-planning", "day-planner", -1, label="x", speech="S?", question="S?")
        self.briefing.consider(new, DIRECTORY, announce=self.announced.append)
        self.assertEqual(self.announced, [])


class _Room:
    """The voice room, as the hook sees it."""

    def __init__(self, focus=None, listening=True):
        self._focus = focus or DIRECTORY.lobby
        self.listening = listening
        self.lines, self.switches = [], []

    def directory(self):
        return DIRECTORY

    def focus(self):
        return self._focus

    async def switch_to(self, focus, say):
        self.switches.append(focus.key)
        self._focus = focus
        return True

    def say_when_idle(self, line):
        self.lines.append(line)

    def someone_listening(self):
        return self.listening


class TestAsAHook(unittest.IsolatedAsyncioTestCase):
    """How the briefing joins the voice room (core/voice/hooks.py)."""

    def setUp(self):
        self.store = InboxStore()

        async def write(r, name):
            return "William here. Merge the notes?", "Merge the notes?"

        self.hook = VoiceBriefing(store=self.store, writer=SpeechWriter(write=write), clock=lambda: JOINED,
                                  hour=lambda: HOUR)
        self.room = _Room()

    async def test_joining_greets_with_the_digest_and_listens_for_new_rows(self):
        self.store.put(DAISY)
        self.assertEqual(self.hook.joined(self.room),
                         "Morning. One agent has something for you. Daisy is waiting for your answer. "
                         "Start with Daisy? Say 'start with Daisy' to begin.")
        self.assertTrue(self.hook.active)

    async def test_nothing_unfinished_leaves_the_default_greeting(self):
        self.assertIsNone(self.hook.joined(self.room))

    async def test_next_agent_switches_through_the_room(self):
        self.store.put(DAISY)
        self.hook.joined(self.room)
        say = AsyncMock()
        self.assertTrue(await self.hook.handle(self.room, "Next agent.", say))
        self.assertEqual(self.room.switches, ["day-planning"])
        say.assert_not_awaited()

    async def test_next_agent_with_nothing_left(self):
        self.hook.joined(self.room)
        say = AsyncMock()
        self.assertTrue(await self.hook.handle(self.room, "next agent", say))
        say.assert_awaited_once_with("That's everything.", "main")
        self.assertEqual(self.room.switches, [])

    async def test_anything_else_is_not_the_hooks(self):
        self.assertFalse(await self.hook.handle(self.room, "Switch me to Daisy", AsyncMock()))

    async def test_arriving_speaks_what_was_left_then_marks_it_heard(self):
        self.store.put(DAISY)
        self.hook.joined(self.room)
        speak = AsyncMock()
        await self.hook.arrived(self.room, DIRECTORY.get("day-planning"), speak)
        speak.assert_awaited_once_with(DAISY.speech)
        self.assertIs(self.store.get("day-planning").status, Status.AWAITING_REPLY)

    async def test_a_voice_turn_finishes_the_channels_row(self):
        self.store.put(DAISY)
        self.hook.turn(self.room, DIRECTORY.get("day-planning"))
        row = self.store.get("day-planning")
        self.assertEqual((row.status, row.status_via.value), (Status.DONE, "VOICE"))

    async def test_a_new_row_is_announced_through_the_room_only_while_someone_listens(self):
        from core.inbox import writer
        self.hook.joined(self.room)
        new = row("day-planning", "day-planner", -1, label="x", speech="Daisy here. Lunch at noon?",
                  question="Lunch at noon?")
        self.store.put(new)
        writer._notify(new)
        self.assertEqual(self.room.lines, ["Daisy has a question for you. Say 'switch to Daisy' to hear it."])

        self.room.listening = False
        newer = row("market-scout", "market-scout", -1, priority=Priority.HIGHEST, label="a big move", speech="S.")
        self.store.put(newer)
        writer._notify(newer)
        self.assertEqual(len(self.room.lines), 1)

    async def test_leaving_ends_the_drive(self):
        self.hook.joined(self.room)
        self.hook.left(self.room)
        self.assertFalse(self.hook.active)


if __name__ == "__main__":
    unittest.main()
