#!/usr/bin/env python3
"""Would reusing one Gemini client across turns pay? From Discord:

    !python model_client_bench.py
    !python model_client_bench.py --rounds 8 --idle 30 --model FLASH

Every turn builds a new chat model today (core/runtime/model/chat.py), and
each builds its own Gemini client with its own connections, so a turn's first
model call may set one up (DNS, TCP, TLS) before it sends anything. This times,
on this machine:

- building the chat model, which in the bot runs on the event loop;
- a short call on a model built just before it ("fresh");
- the same call on one model built once and kept ("reused"), back to back,
  and again after --idle seconds of quiet, since an HTTP client closes idle
  connections after a while, and turns come seconds to minutes apart.

For each: the time to the first streamed chunk, and to the end. Rounds take
the arms in turn, so the network's ups and downs fall on all of them. It makes
3 x rounds + 1 short calls (16 by default), on the cheapest model unless told
otherwise, and changes nothing.
"""
import argparse
import asyncio
import os
import statistics
import sys
import time
from typing import Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# `!python` runs it without `--yes`: it only calls the model.
READ_ONLY = True

PROMPT = "Reply with the single word: ready."
ARMS = ("fresh", "reused", "reused_idle")


def build_model(model: str):
    """A chat model as chat_model() builds one for a Gemini agent."""
    from core.config.settings import Config
    from core.runtime.model.retry import RetryingChatGoogleGenerativeAI
    return RetryingChatGoogleGenerativeAI(model=model, max_retries=Config().llm_max_retries)


async def timed_call(llm) -> Tuple[float, float]:
    """(ms to the first streamed chunk, ms to the end) of one short call."""
    started = time.perf_counter()
    first = None
    async for _ in llm.astream(PROMPT):
        if first is None:
            first = time.perf_counter()
    ended = time.perf_counter()
    return ((first or ended) - started) * 1000, (ended - started) * 1000


async def measure(model: str, rounds: int, idle: float, build: Callable = build_model,
                  call: Callable = timed_call, sleep: Callable = asyncio.sleep) -> Dict[str, List]:
    """The build times (ms) and each arm's (first chunk, whole call) times."""
    builds: List[float] = []

    def built():
        started = time.perf_counter()
        llm = build(model)
        builds.append((time.perf_counter() - started) * 1000)
        return llm

    kept = built()
    await call(kept)  # its connection set up; not counted
    times: Dict[str, List] = {arm: [] for arm in ARMS}
    for index in range(rounds):
        order = ARMS[index % len(ARMS):] + ARMS[:index % len(ARMS)]
        for arm in order:
            if arm == "fresh":
                times[arm].append(await call(built()))
            elif arm == "reused":
                times[arm].append(await call(kept))
            else:
                await sleep(idle)
                times[arm].append(await call(kept))
    return {"builds": builds, **times}


def _pct(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q / 100 * (len(ordered) - 1))))]


def render(results: Dict[str, List], model: str, rounds: int, idle: float, host: str) -> str:
    def ms(value: Optional[float]) -> str:
        return "-" if value is None else f"{value:.0f}"

    builds = results["builds"][1:] or results["builds"]  # the first one also pays the imports, once per process
    lines = [f"Gemini client reuse: {model}, {rounds} rounds, on {host}",
             f"  Building a chat model per turn (on the event loop, in the bot): p50 {ms(_pct(builds, 50))} ms, "
             f"p90 {ms(_pct(builds, 90))} ms",
             f"  {'':26} {'first chunk p50 / p90':>24} {'whole call p50':>16}"]
    labels = {"fresh": "fresh client", "reused": "reused, back to back", "reused_idle": f"reused, after {idle:g} s idle"}
    first: Dict[str, Optional[float]] = {}
    for arm in ARMS:
        firsts = [f for f, _ in results[arm]]
        wholes = [w for _, w in results[arm]]
        first[arm] = _pct(firsts, 50)
        lines.append(f"  {labels[arm]:26} {ms(first[arm]) + ' / ' + ms(_pct(firsts, 90)) + ' ms':>24} "
                     f"{ms(_pct(wholes, 50)) + ' ms':>16}")
    if first["fresh"] is not None and first["reused_idle"] is not None:
        saved = first["fresh"] - first["reused_idle"]
        lines.append(f"  Reuse would save ~{saved:.0f} ms of a turn's first chunk at that spacing "
                     f"(~{first['fresh'] - first['reused']:.0f} ms back to back), "
                     f"plus ~{statistics.median(builds):.0f} ms of building on the loop.")
    return "\n".join(lines)


async def main(argv: Optional[List[str]] = None) -> int:
    from core.config.models import GOOGLE_TIERS

    parser = argparse.ArgumentParser(description="Time a fresh Gemini client against a reused one.")
    parser.add_argument("--model", default="FLASH_LITE", choices=sorted(GOOGLE_TIERS),
                        help="The tier to call (default FLASH_LITE, the cheapest).")
    parser.add_argument("--rounds", type=int, default=5, help="Rounds of the three arms (default 5).")
    parser.add_argument("--idle", type=float, default=15.0,
                        help="Seconds of quiet before each idle call (default 15).")
    args = parser.parse_args(argv)
    model = GOOGLE_TIERS[args.model]
    print(f"Calling {model}: {3 * args.rounds + 1} short calls, about {args.rounds * args.idle:.0f} s of it idle...")
    results = await measure(model, args.rounds, args.idle)
    print(render(results, model, args.rounds, args.idle, os.uname().nodename))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
