import os
from dotenv import load_dotenv

from core.bot_runner import BotRunner
from core.agent.agents_loader import AgentsLoader
import asyncio

# Load environment variables
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY or GEMINI_API_KEY == "your_gemini_api_key_here":
    print("Warning: GEMINI_API_KEY not set or using placeholder. Please set it in .env")

async def run_bots():
    loader = AgentsLoader()
    agents = loader.list_agents()
    
    tasks = []
    for agent in agents:
        token_key = agent.get("discord_token_key")
        if token_key:
            token = os.getenv(token_key)
            if token and token != "your_discord_bot_token_here":
                print(f"Starting bot for agent {agent.get('id')} with token from {token_key}")
                runner = BotRunner(token, agent.get("id"))
                tasks.append(runner.run_bot())
            else:
                print(f"Skipping agent {agent.get('id')}: Token key {token_key} not found or invalid in env.")
        else:
            print(f"Skipping agent {agent.get('id')}: No discord_token_key defined.")
            
    if tasks:
        await asyncio.gather(*tasks)
    else:
        print("No Discord bots to start.")

if __name__ == "__main__":
    import sys
    asyncio.run(run_bots())
