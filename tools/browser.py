import os
import sys
import asyncio
from langchain_core.tools import tool
from browser_use import Agent, ChatGoogle
from core.util import format_tool_response
from browser_use.browser.profile import BrowserProfile
from browser_use.browser.session import BrowserSession

@tool
def browser(goal: str, port: int = 9222) -> str:
    """
    Perform automated web actions using visual AI navigation.
    Connects to an existing Chrome instance using the provided remote debugging port.
    
    Args:
        goal: The specific objective the agent needs to accomplish (e.g., 'Book table for 2 at 7pm at Balthazar').
        port: Remote debugging port for Chrome (default: 9222).
    """
    try:
        # Wrap async execution since LangGraph tools are typically synchronous
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        payload = loop.run_until_complete(_run_browser(goal, port))
        return format_tool_response("browser", payload=payload, errors="None")
        
    except ImportError as e:
        return format_tool_response("browser", payload="", errors=f"Error: dependencies not installed. Missing: {e}")
    except Exception as e:
         return format_tool_response("browser", payload="", errors=f"Error performing browser action: {e}")

async def _run_browser(goal: str, port: int) -> str:
    config = BrowserProfile(
        cdp_url=f"http://127.0.0.1:{port}"
    )
    browser_inst = BrowserSession(browser_profile=config)
    
    # browser-use requires a multimodal visual LLM - using Gemini as native fallback
    llm = ChatGoogle(model="gemini-3.1-pro-preview") 
    
    agent = Agent(
        task=goal,
        llm=llm,
        browser=browser_inst,
    )
    
    history = await agent.run()
    return f"Browsing completed. Final Result: {history.final_result()}"
