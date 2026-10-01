import sys
import os
import argparse
import asyncio
import faulthandler
import discord
from core.util.config import Config
from core.channel.discord.loader import BotsLoader
from core.loaders.agents_loader import AgentsLoader
from core.scheduler.schedule_runner import ScheduleRunner
from core.channel.discord.ownership import DEV, PROD, Ownership

# Dump Python tracebacks on low-level crashes (SIGSEGV, SIGFPE, SIGABRT, SIGBUS, SIGILL)
faulthandler.enable()

# Setup discord.py logging to capture gateway reconnects, heartbeat lag warnings, and rate limits
if hasattr(discord, "utils") and hasattr(discord.utils, "setup_logging"):
    try:
        discord.utils.setup_logging()
    except Exception:
        pass

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
    parser.add_argument(
        "--dev", action="store_true",
        help="Run as a dev instance: connect every bot, but answer only for agents this "
             "host has claimed in the control thread (see core/channel/discord/ownership.py)",
    )
    parser.add_argument(
        "--agents", type=str, default="",
        help="With --dev: comma-separated agent ids to claim at startup, e.g. day-planner,main",
    )
    return parser.parse_args(args)

def _split_agents(value: str) -> list:
    return [a.strip() for a in (value or "").split(",") if a.strip()]

async def run_bots(dev: bool = False, agents: list = None):
    agents_loader = AgentsLoader()
    bots_loader = BotsLoader()
    agent_ids = agents_loader.list_agent_ids()

    agents = list(agents or [])
    if agents and not dev:
        raise SystemExit("--agents requires --dev")
    unknown = sorted(set(agents) - set(agent_ids))
    if unknown:
        raise SystemExit(f"Unknown agent(s) for --agents: {', '.join(unknown)}. Known: {', '.join(sorted(agent_ids))}")

    ownership = Ownership()
    ownership.configure(role=DEV if dev else PROD, agents=agents)
    if dev:
        print("=== DEV INSTANCE ===")
        print(f"Host '{ownership.host}' claims: {', '.join(agents) or 'nothing yet'}")
        print("Other agents stay on prod. Use [claim <agent>] / [release] / [status] in the control thread.")
        print("====================")

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
        finally:
            # Hand claimed agents back to prod on a clean exit (Ctrl+C).
            tokens = {a: b.discord_token for a, b in bots_loader._bots.items() if b}
            try:
                await asyncio.wait_for(ownership.release_all_mine(tokens), timeout=15)
            except Exception as e:
                print(f"Could not release claims: {e}. Post [release] in the control thread.")
    else:
        print("No Discord bots to start.")

if __name__ == "__main__":
    cli_args = parse_args()

    try:
        asyncio.run(run_bots(dev=cli_args.dev, agents=_split_agents(cli_args.agents)))
    except KeyboardInterrupt:
        print("\nProgram interrupted by user. Exiting.")
