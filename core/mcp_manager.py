import os
import importlib
import contextlib
from fastmcp import FastMCP
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class MCPServerManager:
    def __init__(self, server_name="MCP Tool Server"):
        self.server_name = server_name

    def run_server(self, tools_dir="tools"):
        mcp = FastMCP(self.server_name)
        
        if os.path.isdir(tools_dir):
             for folder in os.listdir(tools_dir):
                  folder_path = os.path.join(tools_dir, folder)
                  if os.path.isdir(folder_path) and not folder.startswith("__"):
                       for filename in os.listdir(folder_path):
                            if filename.endswith(".py") and not filename.startswith("__"):
                                 tool_name = filename[:-3] # Drop .py
                                 module_path = f"tools.{folder}.{tool_name}"
                                 try:
                                      mod = importlib.import_module(module_path)
                                      if hasattr(mod, tool_name):
                                           func = getattr(mod, tool_name)
                                           mcp.tool()(func)
                                           print(f"Loaded tool {tool_name} from {module_path}")
                                 except Exception as e:
                                      print(f"Failed to load tool from {module_path}: {e}")

        mcp.run()

class MCPClientManager:
    @contextlib.asynccontextmanager
    async def get_session(self):
        server_params = StdioServerParameters(
            command="python",
            args=[os.path.join(os.path.dirname(__file__), "..", "main.py"), "--mcp"],
        )
        print("Connecting to MCP server...")
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                print("Initializing MCP session...")
                await session.initialize()
                yield session
