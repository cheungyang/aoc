#!/usr/bin/env python3
"""The nightly dream standup, as a script rather than a prompt.

This replaces a five-step instruction that the orchestrator executed on a
`gemini-3.1-pro` turn every night. The instruction had grown defensive --

    "You MUST pause and WAIT for these tool calls to return.
     DO NOT hallucinate their responses."

-- which is the tell. Fanning a fixed trigger out to a known list of agents and
tabulating the replies is not a reasoning task; asking a model to do it costs a
long pro-model run and can still invent a row. Here the fan-out is a loop and
the table is a format string, so a missing reply is visibly missing.

Run by `script-executor` on a cron. Its stdout becomes the channel message, so
anything printed to stdout is user-facing and anything diagnostic goes to stderr.
"""
import argparse
import asyncio
import datetime
import os
import re
import sys

# Add project root to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.loaders.agents_loader import AgentsLoader
from tools.agent_call import agent_call

DREAM_TRIGGER = "Run your dream skill."

# `script-executor` is a shell runner with no model behind it, so it has no
# memory logs to consolidate. The graph workers are stateless by configuration:
# they keep no session between invocations, so there is nothing for a dream to
# synthesise.
EXCLUDED_AGENTS = {"script-executor"}

# The agents run real model turns. Firing all of them at once is a burst of
# concurrent pro/flash calls against one quota; a small pool keeps the standup
# well inside it while still finishing far faster than sequential execution.
MAX_CONCURRENCY = 4

STATUS_DREAMED = "Dreamed"
STATUS_EMPTY = "No new memories"


def standup_agents(loader):
    """Agents with memory worth consolidating, in a stable order."""
    selected = []
    for agent_id in sorted(loader.list_agent_ids()):
        if agent_id in EXCLUDED_AGENTS:
            continue
        config = loader.get_agent_config(agent_id) or {}
        if config.get("stateless"):
            continue
        selected.append((agent_id, config))
    return selected


def resolve_channel(config):
    """A channel the agent is actually permitted to answer in.

    Delegation enforces `channels` as a permission, so a standup that assumed
    `#general` would silently skip any agent scoped to one channel --
    `property-scout` is exactly that case.
    """
    channels = config.get("channels") or []
    if "*" in channels or "general" in channels:
        return "general"
    return channels[0] if channels else "general"


def extract_tag(tag, text):
    """Returns the contents of `<tag>`, or an empty string."""
    if not text:
        return ""
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def parse_dream_response(raw):
    """Reads the dream skill's IPC XML into (status, learnings).

    A reply that isn't the documented XML is reported as-is rather than guessed
    at: an agent that answered conversationally has not dreamt, and quietly
    rendering it as "No new memories" would hide that.
    """
    payload = extract_tag("payload", raw)
    status = extract_tag("status", payload or raw)
    learnings = extract_tag("learnings", raw)

    if not status:
        if not (raw or "").strip():
            return None, "empty response"
        return None, "no <dream_response> in reply"

    return status, learnings


async def dream(agent_id, config, semaphore):
    """Triggers one agent's dream skill and normalises the outcome."""
    channel = resolve_channel(config)
    async with semaphore:
        try:
            raw = await agent_call.ainvoke({
                "agent_id": agent_id,
                "prompt": DREAM_TRIGGER,
                "channel": channel,
                "caller": "script-executor",
            })
        except Exception as e:  # noqa: BLE001 - reported as a standup row
            return {"agent_id": agent_id, "config": config, "error": str(e)}

    errors = extract_tag("errors", raw)
    if errors and errors.lower() not in ("none", ""):
        return {"agent_id": agent_id, "config": config, "error": errors}

    payload = extract_tag("payload", raw) or raw
    status, learnings = parse_dream_response(payload)
    if status is None:
        return {"agent_id": agent_id, "config": config, "error": learnings}

    return {
        "agent_id": agent_id,
        "config": config,
        "status": status,
        "learnings": learnings,
    }


def render_row(result):
    config = result["config"]
    emoji = config.get("emoji", "🤖")
    name = config.get("name", result["agent_id"])

    if result.get("error"):
        return f"{emoji} {name} | ⚠️ Standup failed: {result['error']}"

    status = (result.get("status") or "").strip()
    learnings = (result.get("learnings") or "").strip()

    if status.lower().startswith(STATUS_DREAMED.lower()):
        detail = learnings or "no notable learnings recorded."
        return f"{emoji} {name} | 🌙 Dreamed -> Key Update: {detail}"

    return f"{emoji} {name} | 💤 No new memories to process today."


def render(results, today=None):
    today = today or datetime.date.today().isoformat()
    lines = [f"**Nightly Dream Standup - {today}**", ""]
    lines.extend(render_row(r) for r in results)
    return "\n".join(lines)


async def run_standup():
    loader = AgentsLoader()
    agents = standup_agents(loader)
    if not agents:
        print("No agents to include in the standup.", file=sys.stderr)
        return ""

    print(f"Dream standup: triggering {len(agents)} agents...", file=sys.stderr)

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    results = await asyncio.gather(
        *(dream(agent_id, config, semaphore) for agent_id, config in agents)
    )
    return render(results)


def main():
    parser = argparse.ArgumentParser(description="Run the nightly dream standup.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the agents that would be triggered, without calling them.",
    )
    args = parser.parse_args()

    if args.dry_run:
        loader = AgentsLoader()
        for agent_id, config in standup_agents(loader):
            print(f"{agent_id} -> #{resolve_channel(config)}")
        return

    summary = asyncio.run(run_standup())
    if summary:
        print(summary)


if __name__ == "__main__":
    main()
