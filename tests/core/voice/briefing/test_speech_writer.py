"""Speech versions written once you're in voice: the worker task and the writer's tasks.

The prompt and its reply parsing are SpeechWriterPrompt's (tests/core/prompts/test_voice.py).
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.channel.response_parser import AgentResponse
from core.storage.channel_inbox import InboxStore
from core.channel import inbox
from core.storage.channel_inbox import Status
from core.voice.briefing.speech_writer import SpeechWriter, write_speech
from tests.core.voice.briefing import loader


class TestWriteSpeech(unittest.IsolatedAsyncioTestCase):
    """One task for the stateless worker: a SpeechWriterPrompt, through `ask_worker`."""

    def _row(self):
        return inbox.record_reply("pkm-wiki", "Should the two notes on habits be merged?", asked_at=None,
                                   agent_id="wiki-gardener", loader=loader())

    async def test_the_worker_writes_it(self):
        response = AgentResponse.from_string(
            "<speech>Should the two notes be merged? <question>Should the two notes be merged?</question></speech>")
        ask = AsyncMock(return_value=SimpleNamespace(text="", response=response))
        with patch("core.prompts.base._worker", new=ask):
            speech, question = await write_speech(self._row(), "William")
        self.assertEqual((speech, question), ("Should the two notes be merged?", "Should the two notes be merged?"))
        self.assertEqual(ask.await_args.kwargs["channel"], "pkm-wiki")
        self.assertEqual(ask.await_args.kwargs["caller"], "speech-writer")
        self.assertIn("Should the two notes on habits be merged?", ask.await_args.args[0])
        self.assertIn("Author: William", ask.await_args.args[0])

    async def test_nothing_when_the_worker_fails(self):
        with patch("core.prompts.base._worker", new=AsyncMock(return_value=None)):
            self.assertEqual(await write_speech(self._row(), "William"), (None, None))


class TestSpeechWriter(unittest.IsolatedAsyncioTestCase):

    def _candidate(self, text="Should the two notes on habits be merged?"):
        return inbox.record_reply("pkm-wiki", text, asked_at=None, agent_id="wiki-gardener", loader=loader())

    async def test_a_written_question_keeps_the_candidate_and_reports_it(self):
        calls, done = [], []

        async def write(row, name):
            calls.append((row.channel, name))
            return "William here. Should the notes be merged?", "Should the notes be merged?"

        speech_writer = SpeechWriter(write=write)
        candidate = self._candidate()
        task = speech_writer.start(candidate, "William", on_done=done.append)
        self.assertIs(speech_writer.start(candidate, "William"), task, "one task per post")
        await task

        self.assertEqual(calls, [("pkm-wiki", "William")])
        stored = InboxStore().get("pkm-wiki")
        self.assertEqual((stored.status, stored.question), (Status.UNREAD, "Should the notes be merged?"))
        self.assertEqual(done, [stored])

    async def test_a_candidate_that_asks_nothing_is_dropped(self):
        async def write(row, name):
            return "William here. I merged the notes.", None

        speech_writer = SpeechWriter(write=write)
        await speech_writer.start(self._candidate("Merged the notes."), "William")
        self.assertIs(InboxStore().get("pkm-wiki").status, Status.DONE)

    async def test_a_failed_write_changes_nothing(self):
        async def write(row, name):
            return None, None

        await SpeechWriter(write=write).start(self._candidate(), "William")
        stored = InboxStore().get("pkm-wiki")
        self.assertTrue(stored.is_candidate)

    async def test_a_switch_can_wait_for_its_channels_speech_version(self):
        release = asyncio.Event()

        async def write(row, name):
            await release.wait()
            return "William here. Merge them?", "Merge them?"

        speech_writer = SpeechWriter(write=write)
        speech_writer.start(self._candidate(), "William")
        self.assertTrue(speech_writer.writing("pkm-wiki"))
        await speech_writer.wait("pkm-wiki", timeout=0.01)  # gives up quietly
        self.assertIsNone(InboxStore().get("pkm-wiki").speech)
        release.set()
        await speech_writer.wait("pkm-wiki", timeout=1)
        self.assertEqual(InboxStore().get("pkm-wiki").speech, "William here. Merge them?")
        self.assertFalse(speech_writer.writing("pkm-wiki"))

    async def test_no_more_than_max_parallel_calls_at_once(self):
        running, peak = 0, 0

        async def write(row, name):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
            return "Spoken.", None

        speech_writer = SpeechWriter(write=write, max_parallel=2)
        tasks = []
        for channel in ("general", "day-planning", "topic-research", "fitness-coach"):
            row = inbox.record_reply(channel, "Text.", asked_at=0, loader=loader())  # late replies
            tasks.append(speech_writer.start(row, "Someone"))
        await asyncio.gather(*tasks)
        self.assertEqual(peak, 2)


if __name__ == "__main__":
    unittest.main()
