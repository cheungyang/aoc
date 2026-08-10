import sys
import argparse
import asyncio
from core.util.config import Config
from core.loaders.bots_loader import BotsLoader
from core.loaders.agents_loader import AgentsLoader
from core.runners.schedule_runner import ScheduleRunner

config = Config()
GEMINI_API_KEY = config.gemini_api_key

if not GEMINI_API_KEY or GEMINI_API_KEY == "your_gemini_api_key_here":
    print("Warning: GEMINI_API_KEY not set or using placeholder. Please set it in .env")

if config.langsmith_tracing:
    if config.langsmith_api_key and config.langsmith_api_key != "your_langsmith_api_key_here":
        print(f"LangSmith tracing: ENABLED (Project: '{config.langsmith_project}', Endpoint: '{config.langsmith_endpoint}')")
    else:
        print("Warning: LANGSMITH_TRACING is set to true, but LANGSMITH_API_KEY is missing or using placeholder.")

def parse_args(args=None):
    parser = argparse.ArgumentParser(description="Run Discord Bots with LangGraph")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode (restrict bots to debug_channel)")
    parser.add_argument("--debug-channel", dest="debug_channel", type=str, default=None, help="Specify debug channel name or ID")
    return parser.parse_args(args)

async def run_bots(is_debug: bool = None, debug_channel: str = None):
    config = Config()
    if is_debug is not None:
        config.is_debug = is_debug
    if debug_channel is not None:
        config.debug_channel = debug_channel

    if config.is_debug:
        print("=== DEBUG MODE ENABLED ===")
        print(f"Discord bots will ONLY listen and respond to channel: '{config.debug_channel}'")
        print("===========================")

    agents_loader = AgentsLoader()
    bots_loader = BotsLoader()
    
    agent_ids = agents_loader.list_agent_ids()
    
    tasks = []
    for agent_id in agent_ids:
        bot = bots_loader.get_bot(agent_id)
        if bot:
            tasks.append(bot.run_bot())
            
    schedule_runner = ScheduleRunner()
    tasks.append(schedule_runner.start())
            
    if tasks:
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            print("\nShutting down bots and runners...")
    else:
        print("No Discord bots to start.")

if __name__ == "__main__":
    cli_args = parse_args()
    if cli_args.debug:
        Config().is_debug = True
    if cli_args.debug_channel is not None:
        Config().debug_channel = cli_args.debug_channel

    try:
        asyncio.run(run_bots())
    except KeyboardInterrupt:
        print("\nProgram interrupted by user. Exiting.")
