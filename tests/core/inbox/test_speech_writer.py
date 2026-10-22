"""Speech versions written once you're in voice: parsing, the prompt, and the writer's tasks."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.inbox import writer
from core.inbox.model import Status
from core.channel.response_parser import AgentResponse
from core.inbox.speech_writer import SpeechWriter, build_prompt, parse_speech, write_speech
from core.inbox.store import InboxStore
from core.util.prompt_util import QUESTION_RULE, SPEECH_RULES
from tests.core.inbox import loader


class TestParsing(unittest.TestCase):

    def test_the_speech_block_and_its_question(self):
        speech, question = parse_speech(
            "<speech>William here. <question>Should the two notes be merged?</question></speech>")
        self.assertEqual(speech, "William here. Should the two notes be merged?")
        self.assertEqual(question, "Should the two notes be merged?")

    def test_the_workers_parsed_reply(self):
        response = AgentResponse.from_string("<speech>Daisy here. <question>Energy, one to ten?</question></speech>")
        self.assertEqual(parse_speech(response), ("Daisy here. Energy, one to ten?", "Energy, one to ten?"))
        self.assertEqual(parse_speech(AgentResponse.from_string("<payload>Plain words.</payload>")),
                         ("Plain words.", None))

    def test_a_reply_without_tags_is_taken_whole(self):
        self.assertEqual(parse_speech("Ted here. Your syllabus is ready."), ("Ted here. Your syllabus is ready.", None))
        self.assertEqual(parse_speech([{"type": "text", "text": "<speech>Hi.</speech>"}]), ("Hi.", None))
        self.assertEqual(parse_speech(""), (None, None))
        self.assertEqual(parse_speech(None), (None, None))


class TestPrompt(unittest.TestCase):

    def test_same_rules_as_voice_mode_question_included(self):
        row = writer.record_reply("pkm-wiki", "Two notes overlap: {habits} and {routines}. Merge?", asked_at=None,
                                  agent_id="wiki-gardener", loader=loader())
        prompt = build_prompt(row, "William")
        self.assertIn(SPEECH_RULES, prompt)
        self.assertIn(QUESTION_RULE, prompt)
        self.assertIn("{habits} and {routines}", prompt, "a post's braces are text, not a template")
        self.assertIn("Author: William", prompt)
        self.assertIn("#pkm-wiki", prompt)
        self.assertIn("<assigned_task>", prompt, "in the worker's playbook format")
        self.assertNotIn("{{", prompt, "every placeholder filled")


class TestWriteSpeech(unittest.IsolatedAsyncioTestCase):
    """One task for the stateless worker, through `ask_worker`."""

    def _row(self):
        return writer.record_reply("pkm-wiki", "Should the two notes on habits be merged?", asked_at=None,
                                   agent_id="wiki-gardener", loader=loader())

    async def test_the_worker_writes_it(self):
        response = AgentResponse.from_string(
            "<speech>Should the two notes be merged? <question>Should the two notes be merged?</question></speech>")
        ask = AsyncMock(return_value=SimpleNamespace(text="", response=response))
        with patch("core.runtime.delegation.ask_worker", new=ask):
            speech, question = await write_speech(self._row(), "William")
        self.assertEqual((speech, question), ("Should the two notes be merged?", "Should the two notes be merged?"))
        self.assertEqual(ask.await_args.kwargs["channel"], "pkm-wiki")
        self.assertEqual(ask.await_args.kwargs["caller"], "speech-writer")
        self.assertIn("Should the two notes on habits be merged?", ask.await_args.args[0])

    async def test_nothing_when_the_worker_fails(self):
        with patch("core.runtime.delegation.ask_worker", new=AsyncMock(return_value=None)):
            self.assertEqual(await write_speech(self._row(), "William"), (None, None))


class TestSpeechWriter(unittest.IsolatedAsyncioTestCase):

    def _candidate(self, text="Should the two notes on habits be merged?"):
        return writer.record_reply("pkm-wiki", text, asked_at=None, agent_id="wiki-gardener", loader=loader())

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
            row = writer.record_reply(channel, "Text.", asked_at=0, loader=loader())  # late replies
            tasks.append(speech_writer.start(row, "Someone"))
        await asyncio.gather(*tasks)
        self.assertEqual(peak, 2)


if __name__ == "__main__":
    unittest.main()
