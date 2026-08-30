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
        mock_run.return_value = MagicMock(stdout="command output", stderr="", returncode=0)
        
        agent = ScriptExecutorAgent("script-executor")
        session = SessionManager.get_session(agent_id="script-executor", source="discord", channel="general")
        output = await agent.execute("script echo hello", session=session)
        
        self.assertIn("Script 'echo hello' executed successfully", output)
        self.assertIn("command output", output)
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
        
        self.assertIn("Script 'ls -la ~' executed successfully", output)
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

if __name__ == "__main__":
    unittest.main()
