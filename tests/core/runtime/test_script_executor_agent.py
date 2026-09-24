import unittest
import os
import sys
from unittest.mock import AsyncMock

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.runtime.script_executor_agent import NO_PROMPTS, ScriptExecutorAgent
from core.loaders.agents_loader import AgentsLoader
from core.runtime.session_manager import SessionManager


def _scripts_of(schedule):
    """Script names in a `"kind": "script"` schedule (string or list, args dropped)."""
    raw = schedule.get("script") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(item).split()[0] for item in raw if str(item).strip()]


class TestScriptExecutorAgent(unittest.IsolatedAsyncioTestCase):

    async def test_prompts_are_refused(self):
        """Its work runs as script schedules; a prompt has nothing to run."""
        agent = ScriptExecutorAgent("script-executor")
        session = SessionManager.get_session(agent_id="script-executor", source="discord", channel="general")
        for prompt in ("tool test_tool {}", "script coding_tick.py", "hello"):
            self.assertEqual(await agent.execute(prompt, session=session), NO_PROMPTS)

    async def test_the_refusal_is_posted_to_the_sender(self):
        # ExecutionContext derives channel_obj from a non-string channel.
        channel = AsyncMock()
        channel.name = "general"

        agent = ScriptExecutorAgent("script-executor")
        session = SessionManager.get_session(agent_id="script-executor", source="discord", channel=channel)
        await agent.execute("hello", session=session)

        channel.send.assert_awaited_once_with(NO_PROMPTS)

    async def test_agents_loader_get_agent(self):
        loader = AgentsLoader()
        agent = loader.get_agent("script-executor")
        self.assertIsInstance(agent, ScriptExecutorAgent)
        schedules = agent.config.get("schedules", [])
        all_scripts = [p for s in schedules for p in _scripts_of(s)]
        self.assertIn("sync_knowledge.py", all_scripts)

    async def test_the_coding_tick_is_scheduled_every_five_minutes(self):
        loader = AgentsLoader()
        agent = loader.get_agent("script-executor")
        entries = [s for s in agent.config.get("schedules", [])
                   if "coding_tick.py" in _scripts_of(s)]

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["cron"], "*/5 * * * *")

    async def test_the_coding_tick_reports_into_the_software_dev_channel(self):
        """The tick is the pipeline's only unprompted voice, so it has to speak
        where the work is being discussed rather than in the general cron log."""
        loader = AgentsLoader()
        agent = loader.get_agent("script-executor")
        entry = next(s for s in agent.config.get("schedules", [])
                     if "coding_tick.py" in _scripts_of(s))

        self.assertEqual(entry["channel"], "software-dev")

    async def test_the_tick_channel_is_one_an_agent_actually_hosts(self):
        """`ScheduleRunner` resolves the channel by looking for an agent whose
        `channel_hosts` claims it. A name that nobody hosts falls through to a
        global search and then to posting nowhere, silently, every five minutes.
        """
        loader = AgentsLoader()
        entry = next(s for s in loader.get_agent("script-executor").config["schedules"]
                     if "coding_tick.py" in _scripts_of(s))

        hosts = set()
        for agent_id in loader.list_agent_ids():
            hosts.update(loader.get_agent(agent_id).get_config("channel_hosts", []))

        self.assertIn(entry["channel"], hosts)

    async def test_the_tick_posts_to_the_channel_not_a_thread(self):
        """A `thread` that does not exist in the target channel makes the runner
        log a fallback line on every single run -- 288 of them a day."""
        loader = AgentsLoader()
        entry = next(s for s in loader.get_agent("script-executor").config["schedules"]
                     if "coding_tick.py" in _scripts_of(s))

        self.assertIsNone(entry.get("thread"))


if __name__ == "__main__":
    unittest.main()
