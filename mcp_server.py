import os
from fastmcp import FastMCP
from tools.git import git_command as _git_command

# Initialize the server
mcp = FastMCP("Concierge Tool Server")

@mcp.tool
def git_command(args: str, path: str = ".") -> str:
    """
    Executes a git command with args in the specified folder path.
    The path should be relative to the agent's workspace root.
    Example: args="log -n 5", path="./agents/concierge"
    """
    return _git_command(args, path)

if __name__ == "__main__":
    mcp.run()
