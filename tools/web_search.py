import os
from langchain_core.tools import tool
import requests


@tool
async def web_search(query: str) -> str:
    """Search the web using the Brave LLM Context API.

    This tool is optimized for AI agents and RAG pipelines, providing pre-extracted
    web content.

    Args:
        query: The search query.

    Returns:
        The search results as a JSON string.
    """
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        return "Error: BRAVE_API_KEY environment variable not set. Please set it to use this tool."

    url = "https://api.search.brave.com/res/v1/llm/context"
    params = {"q": query}
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }

    try:
        # Using requests synchronously as it's a simple call.
        # If async is strictly required, we could use httpx or aiohttp.
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.text
    except Exception as e:
        return f"Error performing search: {e}"
