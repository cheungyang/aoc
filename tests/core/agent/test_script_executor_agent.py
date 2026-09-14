import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock
import subprocess

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.script_executor_agent import ScriptExecutorAgent
from core.loaders.agents_loader import AgentsLoader
from core.agent.session_manager import SessionManager

class TestScriptExecutorAgent(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from core.agent.job_manager import JobManager
        JobManager._instance = None
        from core.loaders.tools_loader import ToolsLoader
        ToolsLoader._instance = None
        
        # Set up mock on singleton instance
        loader = ToolsLoader()
        self.mock_discover = MagicMock(return_value={"test_tool": ""})
        loader._discover_tools = self.mock_discover

    @patch('subprocess.run')
    async def test_execute_script_success(self, mock_run):
        """The script's stdout is the message; the runner adds nothing to it."""
        mock_run.return_value = MagicMock(stdout="command output", stderr="", returncode=0)

        agent = ScriptExecutorAgent("script-executor")
        session = SessionManager.get_session(agent_id="script-executor", source="discord", channel="general")
        output = await agent.execute("script echo hello", session=session)

        self.assertEqual(output, "command output")
        self.assertNotIn("executed successfully", output)
        mock_run.assert_called_once_with(['scripts/echo', 'hello'], capture_output=True, text=True, check=True)

    @patch('importlib.import_module')
    async def test_execute_tool_success(self, mock_import):
        
        mock_tool = AsyncMock()
        mock_tool.ainvoke.return_value = "tool result"
        
        mock_module = MagicMock()
        mock_module.test_tool = mock_tool
        mock_import.return_value = mock_module
        
        agent = ScriptExecutorAgent("script-executor")
        session = SessionManager.get_session(agent_id="script-executor", source="discord", channel="general")
        output = await agent.execute("tool test_tool {\"arg1\": \"val1\"}", session=session)
        
        self.assertIn("Tool test_tool executed successfully", output)
        self.assertIn("tool result", output)
        mock_tool.ainvoke.assert_called_once_with({"arg1": "val1"})

    @patch('importlib.import_module')
    async def test_execute_tool_direct_call_success(self, mock_import):
        
        async def test_tool(arg1):
            return f"direct result {arg1}"
            
        mock_module = MagicMock()
        mock_module.test_tool = test_tool
        mock_import.return_value = mock_module
        
        agent = ScriptExecutorAgent("script-executor")
        session = SessionManager.get_session(agent_id="script-executor", source="discord", channel="general")
        output = await agent.execute("tool test_tool {\"arg1\": \"val1\"}", session=session)
        
        self.assertIn("Tool test_tool executed successfully", output)
        self.assertIn("direct result val1", output)

    @patch('subprocess.run')
    async def test_execute_script_with_tilde(self, mock_run):
        mock_run.return_value = MagicMock(stdout="ls output", stderr="", returncode=0)
        
        agent = ScriptExecutorAgent("script-executor")
        session = SessionManager.get_session(agent_id="script-executor", source="discord", channel="general")
        output = await agent.execute("script ls -la ~", session=session)
        
        self.assertEqual(output, "ls output")
        mock_run.assert_called_once()
        called_args = mock_run.call_args[0][0]
        self.assertEqual(called_args[0], 'scripts/ls')
        self.assertEqual(called_args[2], os.path.expanduser('~'))

    async def test_agents_loader_get_agent(self):
        loader = AgentsLoader()
        agent = loader.get_agent("script-executor")
        self.assertIsInstance(agent, ScriptExecutorAgent)
        schedules = agent.config.get("schedules", [])
        all_prompts = [p for s in schedules for p in s.get("prompt", [])]
        self.assertIn("script sync_knowledge.py", all_prompts)

    @patch('subprocess.run')
    @patch('core.agent.script_executor_agent.JobManager')
    async def test_execute_passes_prompt_to_job_manager(self, mock_job_manager_class, mock_run):
        mock_job_manager = MagicMock()
        mock_job_manager_class.return_value = mock_job_manager
        mock_job_manager.new_job_id.return_value = "test-job-id"
        mock_run.return_value = MagicMock(stdout="output", stderr="", returncode=0)
        
        agent = ScriptExecutorAgent("script-executor")
        session = SessionManager.get_session(agent_id="script-executor", source="discord", channel="general", job_id="test-job-id")
        await agent.execute("script echo hello", session=session)
        
        mock_job_manager.add_job.assert_called_once_with(
            session=session, prompt="script echo hello"
        )

    @patch('subprocess.run')
    async def test_a_script_that_says_nothing_produces_no_message(self, mock_run):
        """A five-minute cron must be silent when there is nothing to report.

        The wrapper line was the noise: "executed successfully" with nothing
        after it, 288 times a day.
        """
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)

        agent = ScriptExecutorAgent("script-executor")
        session = SessionManager.get_session(agent_id="script-executor", source="discord", channel="general")
        output = await agent.execute("script coding_tick.py", session=session)

        self.assertEqual(output, "")

    @patch('subprocess.run')
    async def test_whitespace_only_output_is_also_silence(self, mock_run):
        mock_run.return_value = MagicMock(stdout="\n  \n", stderr="", returncode=0)

        agent = ScriptExecutorAgent("script-executor")
        session = SessionManager.get_session(agent_id="script-executor", source="discord", channel="general")
        output = await agent.execute("script coding_tick.py", session=session)

        self.assertEqual(output, "")

    @patch('subprocess.run')
    async def test_nothing_is_posted_to_the_channel_when_there_is_nothing_to_say(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        # ExecutionContext derives channel_obj from a non-string channel.
        channel = AsyncMock()
        channel.name = "general"

        agent = ScriptExecutorAgent("script-executor")
        session = SessionManager.get_session(agent_id="script-executor", source="discord", channel=channel)
        self.assertIsNotNone(session.channel_obj)
        await agent.execute("script coding_tick.py", session=session)

        channel.send.assert_not_called()

    @patch('subprocess.run')
    async def test_a_failing_script_is_never_silent(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            1, "coding_tick.py", stderr="manifest is not readable JSON"
        )

        agent = ScriptExecutorAgent("script-executor")
        session = SessionManager.get_session(agent_id="script-executor", source="discord", channel="general")
        output = await agent.execute("script coding_tick.py", session=session)

        self.assertIn("manifest is not readable JSON", output)

    @patch('subprocess.run')
    async def test_what_the_script_printed_is_what_the_channel_gets(self, mock_run):
        """No framing around the outcome.

        The tick already writes a finished sentence; wrapping it in "Script
        'coding_tick.py' executed successfully:" made the report look like a
        console dump in a channel people read.
        """
        mock_run.return_value = MagicMock(
            stdout="🧠 `feat_01`: implemented (3 file(s) changed).\n", stderr="", returncode=0
        )
        channel = AsyncMock()
        channel.name = "software-dev"

        agent = ScriptExecutorAgent("script-executor")
        session = SessionManager.get_session(agent_id="script-executor", source="discord", channel=channel)
        await agent.execute("script coding_tick.py", session=session)

        channel.send.assert_awaited_once_with("🧠 `feat_01`: implemented (3 file(s) changed).")

    async def test_the_coding_tick_is_scheduled_every_five_minutes(self):
        loader = AgentsLoader()
        agent = loader.get_agent("script-executor")
        entries = [s for s in agent.config.get("schedules", [])
                   if "script coding_tick.py" in s.get("prompt", [])]

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["cron"], "*/5 * * * *")

    async def test_the_coding_tick_reports_into_the_software_dev_channel(self):
        """The tick is the pipeline's only unprompted voice, so it has to speak
        where the work is being discussed rather than in the general cron log."""
        loader = AgentsLoader()
        agent = loader.get_agent("script-executor")
        entry = next(s for s in agent.config.get("schedules", [])
                     if "script coding_tick.py" in s.get("prompt", []))

        self.assertEqual(entry["channel"], "software-dev")

    async def test_the_tick_channel_is_one_an_agent_actually_hosts(self):
        """`ScheduleRunner` resolves the channel by looking for an agent whose
        `channel_hosts` claims it. A name that nobody hosts falls through to a
        global search and then to posting nowhere, silently, every five minutes.
        """
        loader = AgentsLoader()
        entry = next(s for s in loader.get_agent("script-executor").config["schedules"]
                     if "script coding_tick.py" in s.get("prompt", []))

        hosts = set()
        for agent_id in loader.list_agent_ids():
            hosts.update(loader.get_agent(agent_id).get_config("channel_hosts", []))

        self.assertIn(entry["channel"], hosts)

    async def test_the_tick_posts_to_the_channel_not_a_thread(self):
        """A `thread` that does not exist in the target channel makes the runner
        log a fallback line on every single run -- 288 of them a day."""
        loader = AgentsLoader()
        entry = next(s for s in loader.get_agent("script-executor").config["schedules"]
                     if "script coding_tick.py" in s.get("prompt", []))

        self.assertIsNone(entry.get("thread"))


if __name__ == "__main__":
    unittest.main()
