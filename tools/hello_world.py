from langchain_core.tools import tool
from core.util import format_tool_response


@tool
def hello_world() -> str:
    """
    Returns a standardized hello world greeting message.
    """
    return format_tool_response("hello_world", payload="Hello world, Alva!", errors="None")
