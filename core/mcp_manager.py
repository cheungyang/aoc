import os
import importlib
import contextlib
import sys
from fastmcp import FastMCP
from fastmcp.server.context import Context
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from core.tool_loader import ToolLoader

class MCPServerManager:
    def __init__(self, server_name="MCP Tool Server"):
        self.server_name = server_name

    def run_server(self, tools_dir="tools"):
        mcp = FastMCP(self.server_name)
        
        # Load tools using ToolLoader
        loader = ToolLoader(tools_dir=tools_dir)
        tools = loader.load_tools()
        for func in tools:
            # Register loaded tools
            mcp.add_tool(func)
            print(f"Loaded tool {func.__name__}", file=sys.stderr)
            
        @mcp.tool
        async def configure_session(ctx: Context, allowed_tools: list[str]) -> str:
            """Configure tool visibility for this session."""
            print(f"Configuring session with tools: {allowed_tools}", file=sys.stderr)
            await ctx.disable_components(match_all=True)
            await ctx.enable_components(names=set(allowed_tools))
            return f"Session configured with tools: {allowed_tools}"
            
        # By default, only enable configure_session
        mcp.enable(names={"configure_session"}, only=True)
        
        mcp.run()


class MCPClientManager:
    @contextlib.asynccontextmanager
    async def get_session(self):
        server_params = StdioServerParameters(
            command=sys.executable,
            args=[os.path.join(os.path.dirname(__file__), "..", "main.py"), "--mcp"],
        )
        print("Connecting to MCP server...")
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                print("Initializing MCP session...")
                await session.initialize()
                yield session
