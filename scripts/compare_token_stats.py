#!/usr/bin/env python3
"""
Compares token usage, cache hit rate, and execution latency in sessions/memory.db 
between Historical Baseline vs Post-Optimization period.
"""
import os
import sys
import sqlite3
import datetime
import argparse
from collections import defaultdict

# Inject root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sessions", "memory.db"))
# Default cutoff timestamp: Optimization point (2026-08-24T08:40:00-07:00)
DEFAULT_CUTOFF_TS = 1787585994

def get_stats(db_path=DB_PATH, cutoff_ts=DEFAULT_CUTOFF_TS):
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return None, None

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    tables = [r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ctx_%'").fetchall()]

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

    conn.close()
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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare historical vs current token usage and latency.")
    parser.add_argument("--cutoff", type=int, default=DEFAULT_CUTOFF_TS, help="Unix timestamp dividing historical from current.")
    parser.add_argument("--archive-all", action="store_true", help="Archive all active sessions now so all future runs start in fresh tables.")
    args = parser.parse_args()

    if args.archive_all:
        from core.knowledge.memory.sqlite_session_store import SqliteSessionStore
        store = SqliteSessionStore(db_path=DB_PATH)
        res = store.archive_all_sessions()
        print("Archived all active sessions:")
        print(res)

    print_comparison(cutoff_ts=args.cutoff)
