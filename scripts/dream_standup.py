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

Memory v2: the dream proposes, code disposes. Each dream sees its logs and every
memory entry it may act on (the shared Profile, every topic, its own MEMORY and
FEEDBACK) as `<entry id="e7" ...>` elements, and replies with operations on
those ids. `core.knowledge.memory.dream_ops` validates each op, routes it by
tag, dedups, enforces budgets and records the signals behind suggestions. After
the fan-out, `graph-worker` compacts topics that need it; on Sundays the post
ends with tag/subscription suggestions.

Dreams run on at least FLASH (`tier_floor`), whatever the agent's own tier, and
with `record_memory=False`, so a dream's reply never becomes tomorrow's log.

Run by `script-executor` on a cron. Its stdout becomes the channel message, so
anything printed to stdout is user-facing and anything diagnostic goes to stderr.
"""
import argparse
import asyncio
import contextlib
import datetime
import io
import os
import sys

# Add project root to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.knowledge.memory import dream_ops, store
from core.knowledge.memory import state as mstate
from core.knowledge.memory.entries import TagsError
from core.knowledge.memory.inject import memory_topics
from core.loaders.agents_loader import AgentsLoader
from core.runtime.delegation import stream_delegate
from core.runtime.execution_context import surface_scope
from core.scheduler.script_runner import default_timeout
from core.util.config import Config
from core.util.models import tier_floor
from core.util.time_util import get_local_now
from tools.agent_call import agent_call

DREAM_TRIGGER = "Run your dream skill."

# `script-executor` is a shell runner with no model behind it, so it has no
# memory logs to consolidate. Stateless agents (the graph workers) are skipped
# by `standup_agents`: they keep nothing between runs and write no logs.
EXCLUDED_AGENTS = {"script-executor"}

# The agents run real model turns. Firing all of them at once is a burst of
# concurrent pro/flash calls against one quota; the system-wide
# `Config().max_concurrency` pool keeps the standup well inside it while still
# finishing far faster than sequential execution.
#
# The whole standup runs against one deadline: the scheduler kills this script
# after `default_timeout()` (AOC_SCRIPT_TIMEOUT), and a killed standup posts no
# report and never restores the logs of the dreams still in flight. Stopping a
# margin short of the kill leaves time for that cleanup and for the report. A
# dream that runs out of time keeps its logs and is retried the next night.
DEADLINE_MARGIN = 30

STATUS_DREAMED = dream_ops.STATUS_DREAMED
STATUS_EMPTY = dream_ops.STATUS_EMPTY

# The weakest tier a dream runs on. FLASH_LITE agents compound their mistakes
# night after night in a file every future turn reads; one FLASH call a night
# is the cheapest place to buy judgement.
DREAM_MODEL_FLOOR = "FLASH"

# Compaction is a backend task over shared data, not any agent's own memory:
# it goes to the stateless worker through `agent_call`, like every other
# graph-worker call. The worker keeps no memory and writes no logs, and its
# FLASH tier already meets the dream floor.
COMPACTION_AGENT = "graph-worker"
COMPACTION_CALLER = "script-executor"
COMPACTION_CHANNEL = "general"
COMPACTION_TRIGGER = "Compact these shared memory topics."
SUGGESTION_WEEKDAY = 6  # Sunday

def memory_logs_root(root=None):
    """`<vault>/agents`. The vault is `Config().pkm_dir`, not a path under the
    project: `<project>/pkm` exists only as a symlink on some hosts."""
    return os.path.join(root or Config().pkm_dir, "agents")


def agent_memory_dir(agent_id, root=None):
    return os.path.join(memory_logs_root(root), agent_id)


def agent_logs_dir(agent_id, root=None):
    return os.path.join(agent_memory_dir(agent_id, root), "memory_logs")


def list_logs(agent_id, root=None):
    """Sorted names of an agent's memory log files, hidden files excluded."""
    logs_dir = agent_logs_dir(agent_id, root)
    try:
        entries = os.listdir(logs_dir)
    except OSError:
        return []
    names = []
    for name in entries:
        if name.startswith("."):
            continue
        try:
            if os.path.isfile(os.path.join(logs_dir, name)):
                names.append(name)
        except OSError:
            continue
    return sorted(names)


def agents_with_logs(root=None):
    """Agent ids with at least one memory log.

    A dream consumes and deletes its logs, so "has a log" is exactly "has
    something to dream about". An agent whose dream fails keeps its logs and
    is dreamed again the next night.
    """
    try:
        agent_ids = os.listdir(memory_logs_root(root))
    except OSError:
        return set()
    return {agent_id for agent_id in agent_ids if list_logs(agent_id, root)}


def has_work(ctx):
    """Scheduler gate: skip the whole standup when no eligible agent has a memory log.

    Only agents the standup would dream count: a stray log under a stateless or
    excluded agent must not wake the standup every night for nothing.
    """
    eligible = {agent_id for agent_id, _ in standup_agents(AgentsLoader())}
    agents = agents_with_logs() & eligible
    if not agents:
        return False, "no memory logs to dream about"
    return True, f"memory logs for: {', '.join(sorted(agents))}"


@contextlib.contextmanager
def quiet_stdout(verbose: bool = False):
    """Keeps stdout for the standup report alone.

    The runtime narrates itself on stdout — graph reloads, tool rosters, every
    tool call the worker makes. That is fine in a terminal and wrong here: the
    script-executor posts this script's stdout to Discord verbatim, so the
    narration became the message and the one line that mattered was buried in it.
    Captured chatter is discarded, or sent to stderr under `--verbose`.
    """
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            yield
    finally:
        noise = buffer.getvalue()
        if verbose and noise.strip():
            sys.stderr.write(noise)


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


def snapshot_logs(agent_id, root=None):
    """{name: contents} of every log the dream is about to consume."""
    logs_dir = agent_logs_dir(agent_id, root)
    return {name: store.read_text(os.path.join(logs_dir, name)) for name in list_logs(agent_id, root)}


def build_prompt(dream_input):
    """The dream trigger with everything the agent needs inlined.

    The response format is stated here as well as in the skill: this prompt is
    the contract the parser enforces, and weaker models follow what is in front
    of them more reliably than a loaded skill.
    """
    return (
        f"{DREAM_TRIGGER}\n"
        "Your memory logs and every memory entry you may act on are inlined below. "
        "Do not use any tools: the files are read and written for you. "
        "Do not emit a <system_memory_log>.\n"
        f"{dream_input.xml}\n"
        "Reply with only this XML. Refer to existing entries by id; use add only for "
        "a fact no entry already covers. Entries marked stale=\"true\" must be "
        "confirmed or retired.\n"
        f"{dream_ops.RESPONSE_FORMAT}"
    )


def build_compaction_prompt(compaction_input):
    return (
        f"{COMPACTION_TRIGGER}\n"
        "Merge entries that say the same thing, tighten wording, and retire entries "
        "that are no longer worth their space, so each topic fits well inside its "
        "budget. Pairs under <flagged> were written as near-duplicates. Do not add "
        "new facts. Do not use any tools. Do not emit a <system_memory_log>.\n"
        f"{compaction_input.xml}\n"
        "Reply with only this XML.\n"
        f"{dream_ops.COMPACTION_FORMAT}"
    )


def restore_logs(agent_id, snapshot, root=None):
    """Undoes anything written to the logs while the dream ran.

    The dream call runs with `record_memory=False`, so the runtime doesn't save
    its `<system_memory_log>`. This is the backstop for the other path: a tool
    writing into `memory_logs/` directly. A dream about logs that writes a log
    is one the next night dreams about again, forever, so whatever appeared
    during the call -- a new file, or text appended to one that was
    snapshotted -- is removed.
    """
    logs_dir = agent_logs_dir(agent_id, root)
    for name in list_logs(agent_id, root):
        path = os.path.join(logs_dir, name)
        if name not in snapshot:
            with contextlib.suppress(OSError):
                os.remove(path)
        elif store.read_text(path) != snapshot[name]:
            store.write_atomic(path, snapshot[name])


def delete_logs(agent_id, names, root=None):
    logs_dir = agent_logs_dir(agent_id, root)
    for name in names:
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(logs_dir, name))


def standup_deadline():
    """The event-loop time by which every dream must be finished."""
    return asyncio.get_running_loop().time() + max(default_timeout() - DEADLINE_MARGIN, 1)


async def call_agent(agent_id, config, prompt, timeout):
    """One non-recording, floor-tiered agent turn. Returns the reply text."""
    result = await asyncio.wait_for(
        stream_delegate(
            agent_id=agent_id,
            prompt=prompt,
            channel=resolve_channel(config),
            caller="script-executor",
            record_memory=False,
            model=tier_floor(config.get("model"), DREAM_MODEL_FLOOR),
        ),
        timeout=timeout,
    )
    if not result.ok:
        raise RuntimeError(result.error)
    return result.text


async def dream(agent_id, config, semaphore, deadline, night):
    """Runs one agent's dream end to end and normalises the outcome."""
    failed = {"agent_id": agent_id, "config": config}

    async with semaphore:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return {**failed, "error": "standup deadline reached before this dream started"}
        # Snapshot inside the semaphore: a queued dream must see the logs and
        # entries as they are when it runs, not as they were at the start.
        logs = snapshot_logs(agent_id)
        dream_input = dream_ops.build_input(agent_id, night["tags"], logs, night["today"])
        try:
            raw = await call_agent(agent_id, config, build_prompt(dream_input), remaining)
        except asyncio.TimeoutError:
            restore_logs(agent_id, logs)
            return {**failed, "error": f"timed out: standup deadline reached after {int(remaining)}s"}
        except (Exception, asyncio.CancelledError) as e:
            restore_logs(agent_id, logs)
            return {**failed, "error": str(e) or type(e).__name__}

        # Backstop for tool-level writes; the runtime save is already off.
        restore_logs(agent_id, logs)
        reply = dream_ops.parse_reply(raw)
        if reply.error:
            return {**failed, "error": reply.error}
        try:
            report = dream_ops.apply_reply(
                agent_id, reply, dream_input, night["tag_names"],
                night["subscriptions"].get(agent_id, []), night["today"], night["state"],
            )
        except OSError as e:
            return {**failed, "error": f"could not write memory files: {e}"}
        delete_logs(agent_id, logs)

    night["changed_topics"].update(report.changed_topics)
    return {
        "agent_id": agent_id,
        "config": config,
        "status": reply.status,
        "learnings": reply.learnings,
        "report": report,
    }


async def compact(night, deadline):
    """graph-worker tidies topics that changed tonight and need it. Returns a line or None."""
    tags = sorted(t for t in night["changed_topics"] if dream_ops.needs_compaction(t, night["state"]))
    if not tags:
        return None
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        return f"🧹 Compaction skipped ({', '.join(tags)}): standup deadline reached"
    compaction_input = dream_ops.build_compaction_input(
        tags, night["state"].get("near_duplicates", []), night["today"])
    try:
        # The `agent_call` envelope needs no unwrapping: `parse_reply` finds the
        # <dream_response> inside the payload, and a failed call surfaces as the
        # envelope's <errors>.
        raw = await asyncio.wait_for(
            agent_call.ainvoke({
                "agent_id": COMPACTION_AGENT,
                "prompt": build_compaction_prompt(compaction_input),
                "channel": COMPACTION_CHANNEL,
                "caller": COMPACTION_CALLER,
            }),
            timeout=remaining,
        )
    except (Exception, asyncio.TimeoutError) as e:
        return f"🧹 Compaction failed ({', '.join(tags)}): {str(e) or type(e).__name__}"
    reply = dream_ops.parse_reply(str(raw))
    if reply.error:
        return f"🧹 Compaction failed ({', '.join(tags)}): {reply.error}"
    report = dream_ops.apply_reply(
        COMPACTION_AGENT, reply, compaction_input, night["tag_names"], tags,
        night["today"], night["state"], compaction=True,
    )
    dream_ops.clear_flags(night["state"], tags)
    detail = report.summary()
    return f"🧹 Compacted {', '.join(tags)}" + (f" ({detail})" if detail else "")


def oldest_log_age(agent_id, today, root=None):
    """Days since the oldest dated log, or None."""
    ages = []
    for name in list_logs(agent_id, root):
        try:
            ages.append((today - datetime.date.fromisoformat(name[:10])).days)
        except ValueError:
            continue
    return max(ages) if ages else None


def stall_alerts(agents, today, root=None):
    alerts = []
    for agent_id, _ in agents:
        age = oldest_log_age(agent_id, today, root)
        if age is not None and age > mstate.STALLED_LOG_DAYS:
            alerts.append(f"🚨 {agent_id}: oldest unconsumed memory log is {age} days old")
    return alerts


def weekly_suggestions(night):
    if night["today"].weekday() != SUGGESTION_WEEKDAY:
        return []
    state, subs, today = night["state"], night["subscriptions"], night["today"]
    lines = mstate.suggestions(state, subs, night["tag_names"], today)
    mtimes, writers = {}, {}
    for tag in night["tag_names"]:
        scope = store.topic_scope(tag)
        mtimes[tag] = mstate.file_date(scope.path)
        writers[tag] = {e.src for e in store.load(scope).entries}
    lines += mstate.unused_subscription_suggestions(state, subs, mtimes, writers, today)
    return lines


def render_row(result, state=None):
    config = result["config"]
    emoji = config.get("emoji", "🤖")
    name = config.get("name", result["agent_id"])

    if result.get("error"):
        health = (state or {}).get("dreams", {}).get(result["agent_id"], {})
        failures = int(health.get("consecutive_failures", 0))
        if failures >= mstate.FAILURE_ALERT_AFTER:
            return f"{emoji} {name} | 🚨 Dream failed {failures} nights in a row: {result['error']}"
        return f"{emoji} {name} | ⚠️ Standup failed: {result['error']}"

    status = (result.get("status") or "").strip()
    learnings = (result.get("learnings") or "").strip()
    report = result.get("report")
    detail = report.summary() if report else ""
    suffix = f" ({detail})" if detail else ""

    if status.lower().startswith(STATUS_DREAMED.lower()):
        row = f"{emoji} {name} | 🌙 Dreamed -> Key Update: {learnings or 'no notable learnings recorded.'}{suffix}"
    else:
        row = f"{emoji} {name} | 💤 No new memories to process today.{suffix}"
    if report and report.profile_changes:
        row += "\n" + "\n".join(f"    {change}" for change in report.profile_changes)
    return row


def render(results, today=None, state=None, extra=None, alerts=None, suggestions=None):
    today = today or datetime.date.today().isoformat()
    lines = [f"**Nightly Dream Standup - {today}**", ""]
    lines.extend(render_row(r, state) for r in results)
    if extra:
        lines.extend(extra)
    if alerts:
        lines += ["", "**Alerts**"] + alerts
    if suggestions:
        lines += ["", "**Suggestions**"] + [f"- {s}" for s in suggestions]
    return "\n".join(lines)


def filter_agents_with_logs(agents, root=None):
    """Keeps only the agents that have something to dream about.

    Each dream is a paid model turn; an agent with no memory log would only
    answer "No new memories", so it is not asked at all.
    """
    with_logs = agents_with_logs(root)
    return [(agent_id, config) for agent_id, config in agents if agent_id in with_logs]


def subscriptions_for(agents):
    return {agent_id: memory_topics(config, agent_id=agent_id) for agent_id, config in agents}


async def run_standup(verbose: bool = False, include_all: bool = False):
    with quiet_stdout(verbose=verbose):
        loader = AgentsLoader()
        eligible = standup_agents(loader)
        agents = eligible if include_all else filter_agents_with_logs(eligible)
        if not agents:
            print("No agents to include in the standup.", file=sys.stderr)
            return ""

        try:
            tags = store.load_tags()
        except TagsError as e:
            return f"**Nightly Dream Standup** | ⚠️ Not run: {e}"

        today = get_local_now().date()
        state = mstate.load()
        mstate.prune_events(state, today)
        night = {
            "today": today,
            "tags": tags,
            "tag_names": [name for name, _ in tags],
            "subscriptions": subscriptions_for(eligible),
            "state": state,
            "changed_topics": set(),
        }

        print(f"Dream standup: triggering {len(agents)} agents...", file=sys.stderr)

        semaphore = asyncio.Semaphore(Config().max_concurrency)
        deadline = standup_deadline()
        raw_results = await asyncio.gather(
            *(dream(agent_id, config, semaphore, deadline, night) for agent_id, config in agents),
            return_exceptions=True,
        )

        results = []
        for (agent_id, config), res in zip(agents, raw_results):
            if isinstance(res, BaseException):
                res = {"agent_id": agent_id, "config": config, "error": str(res) or type(res).__name__}
            elif not isinstance(res, dict):
                res = {"agent_id": agent_id, "config": config, "error": f"Unexpected result: {res}"}
            mstate.record_dream(state, agent_id, not res.get("error"), today, res.get("error"))
            results.append(res)

        extra = []
        compaction = await compact(night, deadline)
        if compaction:
            extra.append(compaction)
        alerts = stall_alerts(eligible, today)
        suggestions = weekly_suggestions(night)
        mstate.save(state)

    return render(results, today.isoformat(), state, extra, alerts, suggestions)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the nightly dream standup.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the agents that would be triggered, without calling them.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Write captured runtime narration to stderr.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Dream every eligible agent, even those with no memory logs.",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        loader = AgentsLoader()
        agents = standup_agents(loader)
        if not args.all:
            agents = filter_agents_with_logs(agents)
        for agent_id, config in agents:
            print(f"{agent_id} -> #{resolve_channel(config)}")
        return

    try:
        summary = asyncio.run(
            run_standup(verbose=args.verbose, include_all=args.all)
        )
        if summary:
            print(summary)
    except Exception as e:
        print(f"Standup execution failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    # Run as a script, this is the nightly scheduled standup: the dreams it
    # triggers are booked as scheduled work.
    with surface_scope("scheduled"):
        main()
