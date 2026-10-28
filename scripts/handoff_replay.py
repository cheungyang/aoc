#!/usr/bin/env python3
"""Hand-off replay: real conversation points, a hand-off line each, scored.

The evidence for hand-offs (core/runtime/foundation/addressing.py,
core/runtime/history/handoff.py). Each case is a moment in a real channel's
history and a line you might say there to another agent -- "Ted, create a
syllabus on THIS topic" in #pkm-wiki. A case passes when

  1. the router hands the line to the expected agent, in the expected channel
     (routing.decide; no model), and
  2. that agent, given the line and the capsule, replies about the right
     thing: its reply matches one of `reply_matches` (regexes, any case).

Also reported for each case: whether the capsule itself names the referent
(the same regexes), its size, and the time it took to build -- the latency a
hand-off adds before the agent starts.

The capsule is built from the real sessions/memory.db, read-only, from the
source session's transcript up to `until` (local time; the whole minute
counts). The pruner's summary is left out when the session goes on past
`until`, since it describes a later state. The agent then answers as a
hand-off runs it -- on `<agent>:tool:<home channel>`, with the <caller> tag --
but against a throwaway session DB: no history of its own, read-only tools
(scripts/voice_dryrun.py's guard, plus WITHHELD_TOOLS, which act elsewhere),
and no memory log.

Case shape (sessions/handoff_replay_cases.json). The cases quote your own
conversations, so they live in sessions/, which git ignores, next to the
memory.db they are cut from -- never in the repo (tests/test_repo_privacy.py):

    {"id": "wiki-qmd-syllabus", "from": "pkm-wiki", "until": "2026-09-09 19:46",
     "line": "Ted, create a syllabus on THIS topic",
     "expect": {"agent": "topic-researcher", "channel": "topic-research",
                "referent": "qmd, the local CLI search engine for docs",
                "reply_matches": ["qmd", "search engine"]}}

Usage:
    python scripts/handoff_replay.py                      # every case
    python scripts/handoff_replay.py --dry                # routing and capsules only: no model calls
    python scripts/handoff_replay.py --case wiki-qmd-syllabus --show-capsule
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

CASES_PATH = os.path.join(REPO, "sessions", "handoff_replay_cases.json")
# The target: this many cases of ten.
TARGET = 9
# How long one agent turn may take before its case fails (a turn once hung for hours).
CASE_TIMEOUT_S = 240.0
HOST = "main"
# What the read-only guard (scripts/voice_dryrun.py) leaves alone because these
# tools declare no read set, but which acts outside the throwaway session DB:
# graph_call starts a real graph run, nlm creates NotebookLM notebooks and
# sources, git can commit and push. The replay withholds them, and refuses
# rebuilding the task and project indexes.
WITHHELD_TOOLS = frozenset({"graph_call", "nlm", "git"})
REFUSED_ACTIONS = frozenset({("task_query", "sync"), ("project_query", "sync")})
# A provider quota error ("[429] Resource has been exhausted") says nothing
# about the hand-off, so the turn runs again from scratch after a pause, up to
# QUOTA_RETRIES times. The report counts the retries.
QUOTA_ERROR = re.compile(r"\b429\b|resource has been exhausted", re.IGNORECASE)
QUOTA_RETRIES = 2
QUOTA_PAUSE_S = 60.0


@dataclass
class CaseResult:
    case_id: str
    line: str
    routed_to: Optional[str] = None
    routed_channel: Optional[str] = None
    route_reason: str = ""
    capsule: str = ""
    capsule_ms: float = 0.0
    capsule_names_it: bool = False
    reply: str = ""
    reply_matches: Optional[bool] = None
    blocked_writes: List[tuple] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    retries: int = 0

    @property
    def passed(self) -> bool:
        return not self.failures and self.reply_matches is not False


class _UntilStore:
    """SessionStore.recent_history, as of `until` (epoch seconds)."""

    def __init__(self, store, until: Optional[float]):
        self.store, self.until = store, until

    def recent_history(self, session_id, limit, max_bytes=None):
        rows = self.store.recent_history(session_id, limit=1_000_000, max_bytes=max_bytes)
        if self.until is not None:
            rows = [r for r in rows if r["ts"] <= self.until]
        return rows[-limit:]


class _NoCheckpoint:
    def get_tuple(self, config):
        return None


def load_cases(path: str = CASES_PATH, only: Optional[List[str]] = None) -> List[dict]:
    with open(path, encoding="utf-8") as f:
        cases = json.load(f)
    return [c for c in cases if not only or c["id"] in only]


def until_epoch(until: Optional[str]) -> Optional[float]:
    """'2026-09-09 19:46' (local) -> the end of that minute, in epoch seconds."""
    if not until:
        return None
    from core.util.local_time import get_local_timezone

    start = datetime.strptime(until, "%Y-%m-%d %H:%M").replace(tzinfo=get_local_timezone())
    return (start + timedelta(minutes=1)).timestamp() - 0.001


def matches(patterns: List[str], text: str) -> bool:
    return any(re.search(p, text or "", re.IGNORECASE) for p in patterns)


def withhold_side_effects() -> None:
    """Keeps WITHHELD_TOOLS out of every agent's tools and refuses them, and
    REFUSED_ACTIONS, at the permission check. On top of guard_writes()."""
    from scripts.voice_dryrun import BLOCKED_WRITES
    from core.loaders.tools_loader import ToolsLoader

    get_tools, check_permission = ToolsLoader.get_tools, ToolsLoader.check_permission

    def tools_without(self, ctx):
        return [t for t in get_tools(self, ctx)
                if (getattr(t, "name", None) or getattr(t, "__name__", "")) not in WITHHELD_TOOLS]

    def refuse(self, ctx, tool_id, action_name=None, path=None, **kwargs):
        if tool_id in WITHHELD_TOOLS or (tool_id, action_name) in REFUSED_ACTIONS:
            # Asked with no action, it's "may the agent use it at all?": no,
            # but no write was attempted either.
            if action_name is not None and not kwargs.get("probe"):
                BLOCKED_WRITES.append((tool_id, action_name, path))
                print(f"[handoff-replay] refused {tool_id}.{action_name} (replay)", file=sys.stderr)
            return False
        return check_permission(self, ctx, tool_id, action_name, path, **kwargs)

    ToolsLoader.get_tools = tools_without
    ToolsLoader.check_permission = refuse


def prepare(case: dict, loader) -> CaseResult:
    """Routes the line and builds its capsule, from the real memory.db. No model."""
    from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer
    from core.runtime.foundation.routing import decide
    from core.runtime.history.handoff import ROW_BYTES, build_capsule, source_session
    from core.storage.sessions import SessionStore

    expect = case["expect"]
    result = CaseResult(case_id=case["id"], line=case["line"])
    decision = decide(case["line"], HOST, case["from"], loader.get_agent_config(HOST) or {}, loader)
    result.routed_to, result.routed_channel, result.route_reason = decision.agent_id, decision.channel, decision.reason
    if not decision.is_handoff or (decision.agent_id, decision.channel) != (expect["agent"], expect["channel"]):
        result.failures.append(f"routed {decision.target} -> {decision.agent_id} #{decision.channel}, "
                               f"expected handoff -> {expect['agent']} #{expect['channel']}")

    session = source_session(case["from"], loader=loader)
    if session is None:
        result.failures.append(f"no session answers #{case['from']}")
        return result
    until = until_epoch(case.get("until"))
    store = SessionStore()
    goes_on = until is not None and any(
        r["ts"] > until for r in store.recent_history(session.session_id, limit=1, max_bytes=ROW_BYTES))
    now = datetime.fromtimestamp(until).astimezone() if until is not None else None
    name = (loader.get_agent_config(session.agent_id) or {}).get("name")

    started = time.perf_counter()
    result.capsule = build_capsule(session, agent_name=name, store=_UntilStore(store, until),
                                   checkpointer=_NoCheckpoint() if goes_on else SqliteCheckpointer(), now=now)
    result.capsule_ms = (time.perf_counter() - started) * 1000
    result.capsule_names_it = matches(expect["reply_matches"], result.capsule)
    if not result.capsule:
        result.failures.append("empty capsule")
    return result


async def answer(case: dict, result: CaseResult, db_path: str, timeout: float = None,
                 pause: float = QUOTA_PAUSE_S) -> None:
    """The named agent's reply to the line and its capsule, as a hand-off runs it.
    A turn still running after `timeout` seconds fails the case; what it had
    streamed by then is judged all the same. A turn the provider's quota
    stopped runs again from scratch after `pause` seconds (QUOTA_RETRIES);
    only the last run is judged."""
    from scripts.voice_dryrun import BLOCKED_WRITES, reset_history
    from core.loaders.agents_loader import AgentsLoader
    from core.runtime.delegation import with_caller
    from core.runtime.foundation import events
    from core.runtime.foundation.sessions import SessionManager
    from core.runtime.history.handoff import with_capsule

    agent_id, channel = case["expect"]["agent"], case["expect"]["channel"]
    prompt = with_caller(with_capsule(case["line"], result.capsule), HOST)
    timeout = timeout or CASE_TIMEOUT_S

    for attempt in range(QUOTA_RETRIES + 1):
        reset_history(db_path)
        BLOCKED_WRITES.clear()
        session = SessionManager.get_session(agent_id=agent_id, source="tool", channel=channel, record_memory=False)
        tokens, final, errors = [], [], []

        async def run():
            async for event in AgentsLoader().get_agent(agent_id).execute_stream(prompt, session=session):
                if event.get("type") == events.TOKEN:
                    tokens.append(event.get("content", ""))
                elif event.get("type") == events.FINAL_RESPONSE:
                    final.append(event.get("text", "") or "")
                elif event.get("type") == events.ERROR:
                    errors.append(f"agent error: {event.get('content')}")

        try:
            await asyncio.wait_for(run(), timeout)
        except asyncio.TimeoutError:
            errors.append(f"no reply within {timeout:.0f}s")
        if attempt == QUOTA_RETRIES or not any(QUOTA_ERROR.search(e) for e in errors):
            break
        result.retries += 1
        print(f"[handoff-replay] {case['id']}: provider quota error; again in {pause:.0f}s", file=sys.stderr)
        await asyncio.sleep(pause)

    result.failures += errors
    result.reply = (final[-1] if final else "") or "".join(tokens)
    result.reply_matches = matches(case["expect"]["reply_matches"], result.reply)
    if not result.reply_matches:
        result.failures.append(f"reply doesn't name {case['expect']['referent']!r}")
    result.blocked_writes = list(BLOCKED_WRITES)
    verdict = "pass" if result.passed else f"FAIL ({'; '.join(result.failures)})"
    print(f"[handoff-replay] {case['id']}: {verdict}", file=sys.stderr)


def render(cases: List[dict], results: List[CaseResult], dry: bool, show_capsule: bool) -> str:
    out = ["| Case | From | To | Routed | Capsule | Build | Names it | Reply |", "|---|---|---|---|---|---|---|---|"]
    for case, r in zip(cases, results):
        routed = "✅" if r.routed_to == case["expect"]["agent"] and r.routed_channel == case["expect"]["channel"] else "❌"
        reply = "—" if r.reply_matches is None else ("✅" if r.reply_matches else "❌")
        out.append(f"| {r.case_id} | #{case['from']} | {case['expect']['agent']} #{case['expect']['channel']} | "
                   f"{routed} | {len(r.capsule) // 4} tok | {r.capsule_ms:.1f} ms | "
                   f"{'✅' if r.capsule_names_it else '❌'} | {reply} |")
    out.append("")
    for case, r in zip(cases, results):
        out.append(f"### {r.case_id}: {r.line!r}")
        out.append(f"- expected: {case['expect']['referent']}")
        out.append(f"- route: {r.route_reason}")
        if r.reply:
            out.append(f"- reply: {' '.join(r.reply.split())[:400]}")
        if r.blocked_writes:
            out.append(f"- writes refused: {r.blocked_writes}")
        if r.retries:
            out.append(f"- run again after a provider quota error: {r.retries}×")
        for failure in r.failures:
            out.append(f"- ❌ {failure}")
        if show_capsule:
            out += ["", "```xml", r.capsule, "```"]
        out.append("")
    builds = sorted(r.capsule_ms for r in results)
    if builds:
        out.append(f"Capsule build: p50 {builds[len(builds) // 2]:.1f} ms, max {builds[-1]:.1f} ms.")
    if not dry:
        passed = sum(r.passed for r in results)
        out.append(f"Passed {passed} of {len(results)} (target: {TARGET} of 10).")
    return "\n".join(out)


async def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", default=CASES_PATH)
    parser.add_argument("--case", dest="only", action="append", help="Run only this case (repeatable).")
    parser.add_argument("--dry", action="store_true", help="Route and build capsules only; no model calls.")
    parser.add_argument("--show-capsule", action="store_true")
    parser.add_argument("--state-dir", default=None, help="Where the throwaway session DB goes.")
    parser.add_argument("--out", help="Write the report to this path too.")
    args = parser.parse_args(argv)

    from core.loaders.agents_loader import AgentsLoader

    cases = load_cases(args.cases, args.only)
    if not cases:
        print("No cases to run.")
        return 1
    loader = AgentsLoader()
    results = [prepare(case, loader) for case in cases]  # before the session DB is swapped out

    if not args.dry:
        import tempfile

        from scripts.voice_dryrun import guard_writes, isolate_sessions
        from core.runtime import bootstrap

        db_path = isolate_sessions(args.state_dir or tempfile.mkdtemp(prefix="aoc-handoff-replay-"))
        bootstrap.wire()
        guard_writes()
        withhold_side_effects()
        for case, result in zip(cases, results):
            if result.capsule:
                print(f"[handoff-replay] {case['id']}: {case['line']!r}", file=sys.stderr)
                await answer(case, result, db_path)

    report = render(cases, results, args.dry, args.show_capsule)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report + "\n")
    if args.dry:
        return 0 if all(not r.failures for r in results) else 2
    return 0 if sum(r.passed for r in results) >= min(TARGET, len(results)) else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
