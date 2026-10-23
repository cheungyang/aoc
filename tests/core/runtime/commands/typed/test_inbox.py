"""`!inbox`: listing the channel inbox and marking a row done."""
import unittest
from unittest.mock import AsyncMock, MagicMock

from core.runtime.commands.base import TypedContext
from core.storage.channel_inbox import InboxRow, InboxStore, Priority, Source, Status
from core.runtime.commands.typed.inbox import USAGE, InboxCommand, handle_inbox_command

NOW = 1_000_000.0


def put(channel, minutes_ago, **kw):
    base = dict(channel=channel, agent_id="day-planner", posted_at=NOW - minutes_ago * 60, source=Source.SCHEDULE)
    base.update(kw)
    InboxStore().put(InboxRow(**base))


class TestInboxCommand(unittest.TestCase):

    def test_an_empty_inbox(self):
        self.assertEqual(handle_inbox_command("", "general", now=NOW), "📭 The inbox is empty.")

    def test_the_list_shows_each_rows_status_label_and_question(self):
        put("day-planning", 90, label="your morning check-in", speech="Daisy here.", question="How's your energy?")
        put("fitness-coach", 5, agent_id="fitness-coach", priority=Priority.LOW, label="today's workout",
            status=Status.DONE, speech="Frankie here.")
        put("pkm-wiki", 2, agent_id="wiki-gardener", source=Source.REPLY, label="a question for you")

        text = handle_inbox_command(None, "general", now=NOW)
        lines = text.splitlines()
        self.assertEqual(lines[0], "📬 **Inbox**: 2 unfinished of 3")
        self.assertIn("`#pkm-wiki` · wiki-gardener · **UNREAD** · DEFAULT · REPLY", lines[1])
        self.assertIn("speech not written yet", lines[1])
        self.assertIn("`#fitness-coach` · fitness-coach · **DONE** · LOW", lines[2])
        self.assertIn("“your morning check-in” · 1h ago", lines[3])
        self.assertEqual(lines[4], "  ❓ How's your energy?")

    def test_done_marks_this_channel_or_the_one_named(self):
        put("day-planning", 1)
        put("topic-research", 1, agent_id="topic-researcher")
        self.assertEqual(handle_inbox_command("done", "day-planning", now=NOW), "✅ #day-planning is done.")
        self.assertEqual(handle_inbox_command("done #topic-research", "general", now=NOW), "✅ #topic-research is done.")
        self.assertEqual(handle_inbox_command("done", "day-planning", now=NOW), "Nothing unfinished in #day-planning.")
        self.assertIs(InboxStore().get("topic-research").status, Status.DONE)

    def test_anything_else_gets_the_usage(self):
        self.assertEqual(handle_inbox_command("clear all", "general", now=NOW), USAGE)
        self.assertEqual(handle_inbox_command("done", None, now=NOW), USAGE)


class TestInboxCommandOnTheBot(unittest.IsolatedAsyncioTestCase):

    def test_only_the_voice_host_answers(self):
        room = MagicMock()
        for hosts in (True, False):
            room.hosts_voice.return_value = hosts
            self.assertIs(InboxCommand().available(TypedContext(voice=room)), hosts)

    async def test_done_typed_in_a_channel_marks_that_channels_row(self):
        put("day-planning", 1)
        channel = MagicMock(send=AsyncMock())
        channel.name = "Day-Planning"
        await InboxCommand().run(TypedContext(channel=channel, voice=MagicMock()), "done")
        channel.send.assert_awaited_once_with("✅ #day-planning is done.")


if __name__ == "__main__":
    unittest.main()
