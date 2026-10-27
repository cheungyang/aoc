#!/usr/bin/env python3
"""
Compares token usage, cache hit rate, and execution latency in sessions/memory.db 
between Historical Baseline vs Post-Optimization period.
"""
import os
import sys
import datetime
import argparse
from collections import defaultdict

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.storage.sessions import SessionStore  # noqa: E402

# Default cutoff timestamp: Optimization point (2026-08-24T08:40:00-07:00)
DEFAULT_CUTOFF_TS = 1787585994

def get_stats(db_path=None, cutoff_ts=DEFAULT_CUTOFF_TS):
    store = SessionStore(db_path)
    if not store.exists():
        print(f"Database not found at {store.path}")
        return None, None

    with store.read_only() as conn:
        cursor = conn.cursor()
        tables = store.tables(include_archived=True, conn=conn)

        periods = {
            'historical': {'calls': 0, 'in_tokens': 0, 'out_tokens': 0, 'cached_weighted': 0.0, 'exec_time': 0.0, 'exec_calls': 0, 'agents': defaultdict(lambda: [0, 0, 0, 0])},
            'current': {'calls': 0, 'in_tokens': 0, 'out_tokens': 0, 'cached_weighted': 0.0, 'exec_time': 0.0, 'exec_calls': 0, 'agents': defaultdict(lambda: [0, 0, 0, 0])}
        }

        known_agents = [
            'agent-designer', 'day-planner', 'excursion-planner', 'goal-setter',
            'graph-worker', 'main', 'meal-planner', 'property-scout',
            'receipt-processor', 'reward-travel', 'script-executor',
            'software-planner', 'topic-researcher', 'wiki-gardener',
            'brand-editor', 'content-creator', 'software-coder', 'software-qa',
            'knowledge-retriever'
        ]
        known_agents_norm = {a.replace('-', '_'): a for a in known_agents}

        for t in tables:
            # Determine agent name
            raw = t[4:].split('_archived_')[0]
            matched_agent = None
            for norm_a in sorted(known_agents_norm.keys(), key=len, reverse=True):
                if raw.startswith(norm_a):
                    matched_agent = known_agents_norm[norm_a]
                    break
            if not matched_agent:
                matched_agent = raw.split('_')[0]

            cursor.execute(f"""
                SELECT input_tokens, output_tokens, cached_tokens, execution_time, created_at 
                FROM "{t}" 
                WHERE entry_type = 'token'
            """)
            for in_tok, out_tok, cached_pct, exec_t, ts in cursor.fetchall():
                in_tok = in_tok or 0
                out_tok = out_tok or 0
                cached_pct = cached_pct or 0.0
                exec_t = exec_t or 0.0
                tot = in_tok + out_tok

                bucket = 'historical' if ts < cutoff_ts else 'current'
                p = periods[bucket]
                p['calls'] += 1
                p['in_tokens'] += in_tok
                p['out_tokens'] += out_tok
                p['cached_weighted'] += in_tok * (cached_pct / 100.0)
                if exec_t > 0:
                    p['exec_time'] += exec_t
                    p['exec_calls'] += 1

                p['agents'][matched_agent][0] += tot
                p['agents'][matched_agent][1] += 1
                p['agents'][matched_agent][2] += in_tok
                p['agents'][matched_agent][3] += in_tok * (cached_pct / 100.0)

    return periods['historical'], periods['current']

def print_comparison(cutoff_ts=DEFAULT_CUTOFF_TS):
    hist, curr = get_stats(cutoff_ts=cutoff_ts)
    if not hist or not curr:
        return

    cutoff_date = datetime.datetime.fromtimestamp(cutoff_ts).strftime('%Y-%m-%d %H:%M:%S')
    print("=" * 85)
    print(f"TOKEN & LATENCY BENCHMARK: HISTORICAL VS POST-OPTIMIZATION")
    print(f"Cutoff Timestamp: {cutoff_ts} ({cutoff_date})")
    print("=" * 85)

    hist_tot = hist['in_tokens'] + hist['out_tokens']
    curr_tot = curr['in_tokens'] + curr['out_tokens']
    hist_cache = (hist['cached_weighted'] / hist['in_tokens'] * 100) if hist['in_tokens'] > 0 else 0
    curr_cache = (curr['cached_weighted'] / curr['in_tokens'] * 100) if curr['in_tokens'] > 0 else 0

    hist_avg_in = (hist['in_tokens'] / hist['calls']) if hist['calls'] > 0 else 0
    curr_avg_in = (curr['in_tokens'] / curr['calls']) if curr['calls'] > 0 else 0

    hist_avg_lat = (hist['exec_time'] / hist['exec_calls']) if hist['exec_calls'] > 0 else 0
    curr_avg_lat = (curr['exec_time'] / curr['exec_calls']) if curr['exec_calls'] > 0 else 0

    print(f"\n{'Metric':<30} | {'Historical (Pre-Opt)':<22} | {'New (Post-Opt)':<22}")
    print("-" * 85)
    print(f"{'Total LLM Invocations':<30} | {hist['calls']:>22,d} | {curr['calls']:>22,d}")
    print(f"{'Total Tokens Consumed':<30} | {hist_tot:>22,d} | {curr_tot:>22,d}")
    print(f"{'Input Tokens':<30} | {hist['in_tokens']:>22,d} | {curr['in_tokens']:>22,d}")
    print(f"{'Output Tokens':<30} | {hist['out_tokens']:>22,d} | {curr['out_tokens']:>22,d}")
    print(f"{'Avg Input Tokens / Call':<30} | {hist_avg_in:>22,.0f} | {curr_avg_in:>22,.0f}")
    print(f"{'Prompt Cache Hit Rate':<30} | {hist_cache:>21.1f}% | {curr_cache:>21.1f}%")
    if curr['exec_calls'] > 0:
        print(f"{'Avg LLM Latency (Seconds)':<30} | {'N/A (Untracked)':>22} | {curr_avg_lat:>21.2f}s")
    else:
        print(f"{'Avg LLM Latency (Seconds)':<30} | {'N/A (Untracked)':>22} | {'Pending New Calls':>22}")

    print("\n" + "=" * 85)
    print("TOP AGENT BREAKDOWN (Post-Optimization Activity)")
    print("=" * 85)
    if not curr['agents']:
        print("  No new LLM calls recorded yet after the cutoff timestamp.")
    else:
        print(f"{'Agent':<25} | {'Tokens':<12} | {'Calls':<8} | {'Avg Tokens/Call':<16} | {'Cache Rate':<10}")
        print("-" * 85)
        for agent, (tot, calls, in_tok, cached_w) in sorted(curr['agents'].items(), key=lambda x: x[1][0], reverse=True):
            avg_tok = tot / calls if calls > 0 else 0
            crate = (cached_w / in_tok * 100) if in_tok > 0 else 0
            print(f"{agent:<25} | {tot:>12,d} | {calls:>8d} | {avg_tok:>16,.0f} | {crate:>9.1f}%")


def _percentile(values, pct):
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * pct // 100))
    return ordered[int(rank) - 1]


def _agent_for_table(table, agent_ids):
    """The agent a `ctx_<session>` table belongs to (session ids start with the agent id)."""
    raw = table[4:]
    for agent_id in sorted(agent_ids, key=len, reverse=True):
        if raw.startswith(agent_id.replace("-", "_") + "_"):
            return agent_id
    return raw.split("_")[0]


def get_voice_stats(db_path=None, since_ts=0, until_ts=None):
    """Token use of voice turns and the latency of each pipeline stage, since `since_ts`
    (and before `until_ts`, if given: a before/after comparison, as G3's first_token check).

    Token rows are tagged `surface='voice'` (including the agents a voice turn
    delegates to); latency comes from the `turn_trace` rows VoiceManager writes
    beside them (see core/runtime/observability/turn_trace.py).
    """
    import json

    stats = {
        "calls": 0, "in_tokens": 0, "out_tokens": 0,
        "agents": defaultdict(lambda: [0, 0, 0]),   # calls, in, out
        "turns": 0, "outcomes": defaultdict(int), "ttfa": [], "stages": defaultdict(list), "model_calls": [],
    }
    store = SessionStore(db_path)
    if not store.exists():
        print(f"Database not found at {store.path}")
        return None
    agents_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agents")
    agent_ids = os.listdir(agents_dir) if os.path.isdir(agents_dir) else []
    window, params = "created_at >= ?", [since_ts]
    if until_ts:
        window += " AND created_at < ?"
        params.append(until_ts)
    with store.read_only() as conn:
        for t in store.tables(include_archived=True, conn=conn):
            if "surface" not in store.columns(t, conn=conn):
                continue
            agent = _agent_for_table(t, agent_ids)
            for in_tok, out_tok in conn.execute(
                f"""SELECT input_tokens, output_tokens FROM "{t}"
                    WHERE entry_type = 'token' AND surface = 'voice' AND {window}""", params
            ):
                stats["calls"] += 1
                stats["in_tokens"] += in_tok or 0
                stats["out_tokens"] += out_tok or 0
                row = stats["agents"][agent]
                row[0] += 1
                row[1] += in_tok or 0
                row[2] += out_tok or 0
            for (message,) in conn.execute(
                f"""SELECT message FROM "{t}" WHERE entry_type = 'turn_trace' AND {window}""", params
            ):
                try:
                    trace = json.loads(message)
                except (TypeError, ValueError):
                    continue
                stats["turns"] += 1
                stats["outcomes"][trace.get("outcome", "answered")] += 1
                if trace.get("ttfa_ms") is not None:
                    stats["ttfa"].append(trace["ttfa_ms"])
                for stage, delta in (trace.get("deltas") or {}).items():
                    stats["stages"][stage].append(delta)
                stats["model_calls"].append(sum(1 for e in trace.get("events", []) if e.get("kind") == "model_call"))
    return stats


def print_voice_stats(since_ts=0, db_path=None, until_ts=None):
    stats = get_voice_stats(db_path=db_path, since_ts=since_ts, until_ts=until_ts)
    if stats is None:
        return

    def sec(ms):
        return "-" if ms is None else f"{ms / 1000:.2f}s"

    since = datetime.datetime.fromtimestamp(since_ts).strftime('%Y-%m-%d %H:%M') if since_ts else "the beginning"
    until = f" until {datetime.datetime.fromtimestamp(until_ts).strftime('%Y-%m-%d %H:%M')}" if until_ts else ""
    print("=" * 85)
    print(f"VOICE BASELINE since {since}{until}")
    print("=" * 85)
    avg_in = stats["in_tokens"] / stats["calls"] if stats["calls"] else 0
    print(f"{'Voice LLM calls':<30} | {stats['calls']:>12,d}")
    print(f"{'Input / output tokens':<30} | {stats['in_tokens']:>12,d} / {stats['out_tokens']:,d}")
    print(f"{'Avg input tokens / call':<30} | {avg_in:>12,.0f}")
    print(f"{'Voice turns traced':<30} | {stats['turns']:>12,d}   {dict(stats['outcomes'])}")
    if stats["model_calls"]:
        print(f"{'Model calls / turn p50, p90':<30} | {_percentile(stats['model_calls'], 50):>12} , {_percentile(stats['model_calls'], 90)}")
    print(f"{'Time to first audio p50, p90':<30} | {sec(_percentile(stats['ttfa'], 50)):>12} , {sec(_percentile(stats['ttfa'], 90))}")
    if stats["stages"]:
        print(f"\n{'Stage (time since previous)':<30} | {'p50':>8} | {'p90':>8} | {'p95':>8} | {'n':>5}")
        print("-" * 71)
        from core.runtime.observability.turn_trace import STAGES
        for stage in STAGES:
            values = stats["stages"].get(stage)
            if values:
                print(f"{stage:<30} | {sec(_percentile(values, 50)):>8} | {sec(_percentile(values, 90)):>8} | "
                      f"{sec(_percentile(values, 95)):>8} | {len(values):>5}")
    if stats["agents"]:
        print(f"\n{'Agent (voice)':<25} | {'Calls':>8} | {'In tokens':>12} | {'Out tokens':>12}")
        print("-" * 65)
        for agent, (calls, in_tok, out_tok) in sorted(stats["agents"].items(), key=lambda x: x[1][1], reverse=True):
            print(f"{agent:<25} | {calls:>8d} | {in_tok:>12,d} | {out_tok:>12,d}")


# Tool outputs (stored as system messages) that mean a turn hit a wall: the
# filesystem tool's permission refusal, and load_skill's failures.
DENIED_MARKERS = ("does not have permission to perform",)
SKILL_FAILURE_MARKERS = ("Error loading skill", "does not have access to skill", "not found.")


def get_health_stats(db_path=None, since_ts=0):
    """Round trips and dead ends per agent since `since_ts`, from every session table.

    A turn is a user message plus everything up to the next one; its model
    calls are the token rows in between, and its surface is theirs. Denied
    reads and failed load_skill calls are found in the stored tool outputs.
    """
    stats = {"calls": defaultdict(list), "denied": defaultdict(list), "skill_failures": defaultdict(list)}
    store = SessionStore(db_path)
    if not store.exists():
        print(f"Database not found at {store.path}")
        return None
    agents_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agents")
    agent_ids = os.listdir(agents_dir) if os.path.isdir(agents_dir) else []
    with store.read_only() as conn:
        for t in store.tables(include_archived=True, conn=conn):
            if "surface" not in store.columns(t, conn=conn):
                continue
            agent = _agent_for_table(t, agent_ids)
            turn = None  # [model calls, surface]

            def close(turn):
                if turn is not None and turn[0]:
                    stats["calls"][(agent, turn[1] or "?")].append(turn[0])

            rows = conn.execute(
                f"""SELECT entry_type, from_role, message, surface FROM "{t}"
                    WHERE entry_type IN ('message', 'token') AND created_at >= ? ORDER BY id""", (since_ts,)
            )
            for entry_type, role, message, surface in rows:
                if entry_type == "token":
                    if turn is not None:
                        turn[0] += 1
                        turn[1] = turn[1] or surface
                elif role == "user":
                    close(turn)
                    turn = [0, None]
                elif role == "system" and isinstance(message, str) and message.startswith("Tool Output"):
                    if any(m in message for m in DENIED_MARKERS):
                        stats["denied"][agent].append(message[:200])
                    elif "load_skill" in message and any(m in message for m in SKILL_FAILURE_MARKERS):
                        stats["skill_failures"][agent].append(message[:200])
            close(turn)
    return stats


def print_health_stats(since_ts=0, db_path=None):
    """The Phase 2 "done when" checks: round trips per turn, denied reads, failed load_skill."""
    stats = get_health_stats(db_path=db_path, since_ts=since_ts)
    if stats is None:
        return
    since = datetime.datetime.fromtimestamp(since_ts).strftime('%Y-%m-%d %H:%M') if since_ts else "the beginning"
    print("=" * 85)
    print(f"AGENT HEALTH since {since}")
    print("=" * 85)
    print(f"{'Agent':<22} | {'Surface':<9} | {'Turns':>6} | {'Model calls p50':>15} | {'p90':>4} | {'max':>4}")
    print("-" * 75)
    for (agent, surface), calls in sorted(stats["calls"].items()):
        print(f"{agent:<22} | {surface:<9} | {len(calls):>6} | {_percentile(calls, 50):>15} | "
              f"{_percentile(calls, 90):>4} | {max(calls):>4}")
    for title, key in (("Denied tool permissions", "denied"), ("Failed load_skill calls", "skill_failures")):
        total = sum(len(v) for v in stats[key].values())
        print(f"\n{title}: {total}")
        for agent, samples in sorted(stats[key].items()):
            print(f"  {agent}: {len(samples)}  e.g. {samples[-1]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare historical vs current token usage and latency.")
    parser.add_argument("--cutoff", type=int, default=DEFAULT_CUTOFF_TS, help="Unix timestamp dividing historical from current.")
    parser.add_argument("--archive-all", action="store_true", help="Archive all active sessions now so all future runs start in fresh tables.")
    parser.add_argument("--voice", action="store_true", help="Report the voice baseline: voice token use and per-stage latency from turn traces.")
    parser.add_argument("--since", type=int, default=0, help="With --voice: only rows after this Unix timestamp.")
    parser.add_argument("--until", type=int, default=0, help="With --voice: only rows before this Unix timestamp.")
    parser.add_argument("--health", action="store_true",
                        help="Report model calls per user turn by agent, denied tool permissions and failed load_skill calls.")
    parser.add_argument("--days", type=float, default=7, help="With --health: look back this many days (default 7).")
    args = parser.parse_args()

    if args.voice:
        print_voice_stats(since_ts=args.since, until_ts=args.until or None)
        sys.exit(0)

    if args.health:
        print_health_stats(since_ts=datetime.datetime.now().timestamp() - args.days * 86400)
        sys.exit(0)

    if args.archive_all:
        store = SessionStore()
        res = store.archive_all_sessions()
        print("Archived all active sessions:")
        print(res)

    print_comparison(cutoff_ts=args.cutoff)
