import unittest
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock
import subprocess

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from core.agent.script_executor_agent import ScriptExecutorAgent
from core.loaders.agents_loader import AgentsLoader

class TestScriptExecutorAgent(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from core.agent.job_manager import JobManager
        JobManager._instance = None

    @patch('subprocess.run')
    async def test_execute_exec_success(self, mock_run):
        mock_run.return_value = MagicMock(stdout="command output", stderr="", returncode=0)
        
        agent = ScriptExecutorAgent("script-executor")
        output = await agent.execute("exec echo hello", "test_source")
        
        self.assertIn("Command 'echo hello' executed successfully", output)
        self.assertIn("command output", output)
        mock_run.assert_called_once_with(['echo', 'hello'], capture_output=True, text=True, check=True)

    @patch('subprocess.run')
    async def test_execute_script_success(self, mock_run):
        mock_run.return_value = MagicMock(stdout="script output", stderr="", returncode=0)
        
        agent = ScriptExecutorAgent("script-executor")
        output = await agent.execute("script test.sh", "test_source")
        
        self.assertIn("Script scripts/test.sh executed successfully", output)
        self.assertIn("script output", output)
        mock_run.assert_called_once_with(['bash', 'scripts/test.sh'], capture_output=True, text=True, check=True)

    @patch('subprocess.run')
    async def test_execute_script_python_success(self, mock_run):
        mock_run.return_value = MagicMock(stdout="python output", stderr="", returncode=0)
        
        agent = ScriptExecutorAgent("script-executor")
        output = await agent.execute("script test.py", "test_source")
        
        self.assertIn("Script scripts/test.py executed successfully", output)
        self.assertIn("python output", output)
        import sys
        mock_run.assert_called_once_with([sys.executable, 'scripts/test.py'], capture_output=True, text=True, check=True)

    @patch('subprocess.run')
    async def test_execute_exec_with_tilde(self, mock_run):
        mock_run.return_value = MagicMock(stdout="ls output", stderr="", returncode=0)
        
        agent = ScriptExecutorAgent("script-executor")
        output = await agent.execute("exec ls -la ~", "test_source")
        
        self.assertIn("Command 'ls -la ~' executed successfully", output)
        mock_run.assert_called_once()
        called_args = mock_run.call_args[0][0]
        self.assertEqual(called_args[2], os.path.expanduser('~'))

    async def test_agents_loader_get_agent(self):
        loader = AgentsLoader()
        agent = loader.get_agent("script-executor")
        self.assertIsInstance(agent, ScriptExecutorAgent)

if __name__ == "__main__":
    unittest.main()
