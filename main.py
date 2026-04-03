import os
from dotenv import load_dotenv
from core.bot_runner import BotRunner
from core.mcp_manager import MCPServerManager

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not DISCORD_TOKEN or DISCORD_TOKEN == "your_discord_bot_token_here":
    print("Warning: DISCORD_TOKEN not set or using placeholder. Please set it in .env")

if not GEMINI_API_KEY or GEMINI_API_KEY == "your_gemini_api_key_here":
    print("Warning: GEMINI_API_KEY not set or using placeholder. Please set it in .env")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--mcp":
        server_manager = MCPServerManager()
        server_manager.run_server()
    elif DISCORD_TOKEN and DISCORD_TOKEN != "your_discord_bot_token_here":
        import asyncio
        runner = BotRunner(DISCORD_TOKEN)
        asyncio.run(runner.run_bot())
    else:
        print("Cannot start bot: DISCORD_TOKEN is missing or placeholder.")
