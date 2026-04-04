import asyncio
import os
import sys

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.mcp_manager import MCPClientManager
from langchain_mcp_adapters.tools import load_mcp_tools

async def main():
    os.environ["GEMINI_API_KEY"] = "mock_key"
    mcp_client = MCPClientManager()
    async with mcp_client.get_session() as mcp_session:
        from core.agent_builder import AgentBuilder
        builder = AgentBuilder(mcp_session)
        print("Building agent...")
        agent = await builder.build_agent("concierge")
        print("Agent built successfully.")
            # Also check description or other attributes if needed

if __name__ == "__main__":
    asyncio.run(main())
