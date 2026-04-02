import os
import shutil
import tempfile
import asyncio
import unittest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv

load_dotenv()

class TestGitPullAgent(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Create a temp directory for fake git
        self.test_dir = tempfile.mkdtemp()
        self.fake_git_log = os.path.join(self.test_dir, "fake_git.log")
        
        # Create fake git script
        self.fake_git_path = os.path.join(self.test_dir, "git")
        with open(self.fake_git_path, "w") as f:
            f.write(f"""#!/bin/sh
echo "git executed with args: $@" >> {self.fake_git_log}
echo "Already up to date." # Mock success output
exit 0
""")
        os.chmod(self.fake_git_path, 0o755)
        
        # Modify PATH to prepend fake git dir
        self.original_path = os.environ.get("PATH", "")
        os.environ["PATH"] = self.test_dir + os.pathsep + self.original_path

    async def asyncTearDown(self):
        # Restore PATH
        os.environ["PATH"] = self.original_path
        # Clean up temp dir
        shutil.rmtree(self.test_dir)

    async def test_agent_calls_git_pull(self):
        # Path to mcp_server.py is now up one directory
        mcp_server_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "mcp_server.py"))
        server_params = StdioServerParameters(
            command="python",
            args=[mcp_server_path],
        )
        
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await load_mcp_tools(session)
                
                llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash")
                agent = create_react_agent(llm, tools)
                
                # Ask agent to pull. We use "." as path as requested.
                response = await agent.ainvoke({"messages": [HumanMessage(content="perform a git pull on '.'")]})
                
                # Verify that fake git was called
                self.assertTrue(os.path.exists(self.fake_git_log), "Fake git log was not created")
                with open(self.fake_git_log, "r") as f:
                     log_content = f.read()
                
                print(f"\nCaptured Git Log:\n{log_content}")
                self.assertIn("git executed with args: pull", log_content)

if __name__ == "__main__":
    unittest.main()
