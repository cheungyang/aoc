import os
from dotenv import load_dotenv
from core.loaders.bots_loader import BotsLoader
from core.loaders.agents_loader import AgentsLoader
import asyncio

# Load environment variables
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY or GEMINI_API_KEY == "your_gemini_api_key_here":
    print("Warning: GEMINI_API_KEY not set or using placeholder. Please set it in .env")

async def run_bots():
    agents_loader = AgentsLoader()
    bots_loader = BotsLoader()
    
    agent_ids = agents_loader.list_agent_ids()
    
    tasks = []
    for agent_id in agent_ids:
        bot = bots_loader.get_bot(agent_id)
        if bot:
            tasks.append(bot.run_bot())
            
    if tasks:
        await asyncio.gather(*tasks)
    else:
        print("No Discord bots to start.")

if __name__ == "__main__":
    import sys
    asyncio.run(run_bots())
