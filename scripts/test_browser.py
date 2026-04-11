#!/usr/bin/env python
import asyncio
import sys
import os

# Ensure project root is in python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.browser import browser

def main():
    print("Prerequisite: Make sure Chrome is running with remote debugging enabled!")
    print("Command: open -a 'Google Chrome' --args --remote-debugging-port=9222")
    print("-" * 50)
    
    goal = "Open google.com, type 'Hello World' in the search box, and return. Just stage the loop."
    mock_agent_id = "skill-runner"
    
    print(f"Running browser tool with goal: '{goal}'...")
    try:
        # The tool wraps async execution internally, so we call it synchronously
        result = browser.invoke({"goal": goal, "agent_id": mock_agent_id})
        print("\nTool Execution Result:")
        print(result)
    except Exception as e:
        print(f"\nError running tool: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
