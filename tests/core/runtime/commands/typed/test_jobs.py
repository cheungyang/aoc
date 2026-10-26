"""The job commands, typed to the bot: !jobs, !job <id> and !kill <id>.

They answer from JobManager and the session log, with no model, and only
where the bot answers the channel.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.runtime.commands.base import TypedContext
from core.runtime.commands.registry import TYPED
from core.runtime.commands.typed import jobs as jobs_mod
from core.runtime.commands.typed.job import job_text
from core.runtime.commands.typed.jobs import MAX_JOBS, jobs_text, last_reply
from core.runtime.commands.typed.kill import kill_text
from core.runtime.foundation.jobs import Job

NOW = 1_800_000_000.0


def _job(job_id, status="running", agent_id="day-planner", started_ago=300, prompt="plan my week", updated_ago=60):
    return Job(job_id=job_id, agent_id=agent_id, session_id=f"{agent_id}:job:{job_id}",
               started=NOW - started_ago, updated=NOW - updated_ago, status=status, prompt=prompt)


class FakeJobs:
    """What the commands ask of JobManager. `job()` is this process's memory;
    `get_job()` falls back to the store, here `stored`."""

    def __init__(self, *jobs, stored=(), stops_after=None):
        self.memory = {j.job_id: j for j in jobs}
        self.stored = {j.job_id: j for j in stored}
        self.killed = []
        self.stops_after = stops_after  # polls before a killing job honors it
        self.polls = 0

    def get_jobs(self, allowlist=None):
        return [j for j in self.memory.values() if j.status in allowlist]

    def get_job(self, job_id):
        return self.memory.get(job_id) or self.stored.get(job_id)

    def job(self, job_id):
        found = self.memory.get(job_id)
        if found is not None and found.status == "killing" and self.stops_after is not None:
            self.polls += 1
            if self.polls >= self.stops_after:
                found.status = "killed"
        return found

    def kill_job(self, job_id):
        self.killed.append(job_id)
        self.memory[job_id].status = "killing"


class FakeStore:
    def __init__(self, entries=()):
        self.entries = list(entries)

    def load_history(self, session_id, limit=50):
        return self.entries[-limit:]


class TestJobs(unittest.TestCase):

    def test_newest_first_with_status_agent_and_prompt(self):
        manager = FakeJobs(_job("old11111", started_ago=900), _job("new22222", started_ago=60, agent_id="meal-planner"))
        text = jobs_text(manager, now=NOW)

        lines = text.splitlines()
        self.assertEqual(lines[0], "**2 job(s)**")
        self.assertTrue(lines[1].startswith("`new22222` **running** meal-planner"))
        self.assertIn("(1m ago)", lines[1])
        self.assertIn('"plan my week"', lines[1])
        self.assertTrue(lines[2].startswith("`old11111`"))
        self.assertIn("!kill <id>", lines[-1])

    def test_a_job_being_killed_shows_and_finished_ones_do_not(self):
        manager = FakeJobs(_job("a1"), _job("b2", status="killing"), _job("c3", status="completed"),
                           _job("d4", status="killed"), _job("e5", status="error"))
        text = jobs_text(manager, now=NOW)

        for shown in ("a1", "b2", "e5"):
            self.assertIn(f"`{shown}`", text)
        for hidden in ("c3", "d4"):
            self.assertNotIn(f"`{hidden}`", text)

    def test_nothing_running(self):
        self.assertEqual(jobs_text(FakeJobs(), now=NOW), "No active jobs.")

    def test_a_long_list_is_capped(self):
        manager = FakeJobs(*[_job(f"j{i:03d}", started_ago=i + 1) for i in range(MAX_JOBS + 5)])
        text = jobs_text(manager, now=NOW)

        self.assertTrue(text.startswith(f"**{MAX_JOBS + 5} job(s)**, newest {MAX_JOBS} shown"))
        self.assertIn("`j000`", text)
        self.assertNotIn(f"`j{MAX_JOBS + 4:03d}`", text)

    def test_a_long_prompt_stays_on_one_short_line(self):
        manager = FakeJobs(_job("a1", prompt="word " * 100))
        line = jobs_text(manager, now=NOW).splitlines()[1]
        self.assertIn("...", line)
        self.assertLess(len(line), 200)


class TestLastReply(unittest.TestCase):

    def test_the_latest_non_empty_model_reply(self):
        store = FakeStore([
            {"from": "user", "message": "go"},
            {"from": "ai", "message": "Step one done."},
            {"from": "ai", "message": "  "},
            {"from": "user", "message": "and?"},
        ])
        self.assertEqual(last_reply("s", store), "Step one done.")

    def test_none_logged(self):
        self.assertEqual(last_reply("s", FakeStore([{"from": "user", "message": "go"}])), "")


class TestJob(unittest.TestCase):

    def test_record_and_last_reply(self):
        manager = FakeJobs(_job("abc12345", agent_id="topic-researcher", prompt="research tide pools"))
        store = FakeStore([{"from": "ai", "message": "Found 4 sources so far."}])
        text = job_text("abc12345", manager, store, now=NOW)

        self.assertIn("`abc12345` **running** · topic-researcher", text)
        self.assertIn("(5m ago) · updated 1m ago", text)
        self.assertIn("Session: `topic-researcher:job:abc12345`", text)
        self.assertIn("Prompt: research tide pools", text)
        self.assertIn("Last reply: Found 4 sources so far.", text)

    def test_a_finished_job_is_found_in_the_store(self):
        manager = FakeJobs(stored=[_job("done0001", status="completed")])
        self.assertIn("**completed**", job_text("done0001", manager, FakeStore(), now=NOW))

    def test_nothing_logged_yet(self):
        self.assertIn("No reply logged yet.", job_text("a1", FakeJobs(_job("a1")), FakeStore(), now=NOW))

    def test_unknown_and_missing_ids(self):
        self.assertEqual(job_text("nope", FakeJobs(), FakeStore(), now=NOW), "No job `nope`. `!jobs` lists them.")
        self.assertIn("Usage", job_text("  ", FakeJobs(), FakeStore(), now=NOW))


class TestKill(unittest.IsolatedAsyncioTestCase):

    async def test_a_running_job_is_killed_and_shows_its_last_reply(self):
        manager = FakeJobs(_job("abc12345"), stops_after=2)
        store = FakeStore([{"from": "ai", "message": "Halfway through the plan."}])
        text = await kill_text("abc12345", manager, store, wait_s=5, poll_s=0)

        self.assertEqual(manager.killed, ["abc12345"])
        self.assertEqual(text, "🛑 Killed `abc12345` (day-planner).\nLast reply: Halfway through the plan.")

    async def test_a_job_that_has_not_stopped_yet_is_reported_as_asked(self):
        """A job stops at its next safe point; a long model call can outlast the wait."""
        manager = FakeJobs(_job("abc12345"))
        text = await kill_text("abc12345", manager, FakeStore(), wait_s=0, poll_s=0)

        self.assertEqual(manager.killed, ["abc12345"])
        self.assertTrue(text.startswith("⏳ Asked `abc12345` to stop."))

    async def test_a_job_that_finished_meanwhile_says_so(self):
        manager = FakeJobs(_job("abc12345"))

        def finishes(job_id):
            manager.memory[job_id].status = "completed"

        manager.kill_job = finishes
        text = await kill_text("abc12345", manager, FakeStore(), wait_s=1, poll_s=0)
        self.assertEqual(text, "`abc12345` ended as **completed** before the kill took effect.")

    async def test_an_ended_job_is_not_killed_again(self):
        in_memory = FakeJobs(_job("a1", status="error"))
        self.assertEqual(await kill_text("a1", in_memory, FakeStore()), "`a1` already ended: **error**.")
        self.assertEqual(in_memory.killed, [])

        stored = FakeJobs(stored=[_job("b2", status="killed")])
        self.assertEqual(await kill_text("b2", stored, FakeStore()), "`b2` already ended: **killed**.")

    async def test_a_job_this_process_does_not_run_cannot_be_killed(self):
        """The kill is honored in memory, by the process running the job."""
        manager = FakeJobs(stored=[_job("elsewhere", status="running")])
        text = await kill_text("elsewhere", manager, FakeStore())
        self.assertEqual(text, "No job `elsewhere` running here. `!jobs` lists them.")
        self.assertEqual(manager.killed, [])

    async def test_missing_id(self):
        self.assertIn("Usage", await kill_text("", FakeJobs(), FakeStore()))


class TestTheCommands(unittest.IsolatedAsyncioTestCase):
    """Through TYPED, as the Discord runner gives them."""

    def _ctx(self, answers_here=True):
        channel = AsyncMock()
        channel.name = "general"
        return TypedContext(channel=channel, agent_id="main", answers_here=answers_here), channel

    async def test_each_answers_in_the_channel(self):
        manager = FakeJobs(_job("abc12345"), stops_after=1)
        with patch("core.runtime.commands.typed.jobs.JobManager", return_value=manager), \
             patch("core.runtime.commands.typed.job.JobManager", return_value=manager), \
             patch("core.runtime.commands.typed.kill.JobManager", return_value=manager), \
             patch("core.runtime.commands.typed.kill.POLL_SECONDS", 0), \
             patch.object(jobs_mod, "SessionStore", return_value=FakeStore()):
            for text, expected in (("!jobs", "**1 job(s)**"), ("!job abc12345", "Session:"),
                                   ("!kill abc12345", "🛑 Killed `abc12345`")):
                with self.subTest(command=text):
                    ctx, channel = self._ctx()
                    self.assertIsNotNone(await TYPED.answer(text, ctx))
                    self.assertIn(expected, channel.send.await_args.args[0])

    async def test_only_the_bot_that_answers_the_channel_answers(self):
        for text in ("!jobs", "!job abc12345", "!kill abc12345"):
            with self.subTest(command=text):
                ctx, channel = self._ctx(answers_here=False)
                self.assertIsNone(await TYPED.answer(text, ctx))
                channel.send.assert_not_called()

    def test_job_and_jobs_are_different_commands(self):
        self.assertEqual(TYPED.match("!jobs")[0].name, "jobs")
        self.assertEqual(TYPED.match("!job abc")[0].name, "job")


if __name__ == "__main__":
    unittest.main()
