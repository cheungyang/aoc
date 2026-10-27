#!/usr/bin/env python3
"""Proactive replay: labeled shadow decisions, re-run with and without main's memory (D20).

G3 decides whether the Concierge keeps its dream (D20). The dream turns your
team radio labels into main's MEMORY and FEEDBACK, which the Switchboard reads.
This replay measures whether that memory makes the Switchboard better.

Each decision with a labeled route has its stored prompt re-run
(decisions.prompt: the exchange, cards, tags and Board as they were) twice:

  with      main as it is now: MEMORY and FEEDBACK in its system prompt
  without   the same run, with both blocks empty

Each labeled route is then checked against the routes the replay proposes:

  kept      a ✅ route proposed again        lost      a ✅ route not proposed
  repeated  a ❌ route proposed again        avoided   a ❌ route not proposed

Precision is kept / (kept + repeated). Memory earns its keep if "with"
repeats fewer ❌ routes without losing ✅ ones.

Leakage: memory was learned from these same labels, so on decisions the dream
has already read, "with" is flattered. The report gives the scores twice: for
every labeled decision, and for those labeled after main's memory files last
changed, which no dream has read yet. Only the second is a fair test.

The Board is read before anything else, read-only. The runs use a throwaway
session DB, with read-only tools (scripts/voice_dryrun.py's guard) and no
memory log.

Usage:
    python scripts/proactive_replay.py                       # every labeled decision
    python scripts/proactive_replay.py --limit 20 --out report.md
    python scripts/proactive_replay.py --decision 42
"""
import argparse
import asyncio
import contextlib
import os
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

ARMS = ("with", "without")
SWITCHBOARD = "main"


@dataclass
class Labeled:
    """A decision, its prompt, and your labels on its offered routes."""
    decision_id: int
    prompt: str
    labeled_at: float
    labels: Dict[str, str]              # recipient -> good / bad


@dataclass
class Score:
    kept: int = 0
    lost: int = 0
    repeated: int = 0
    avoided: int = 0

    def add(self, other: "Score") -> None:
        self.kept += other.kept
        self.lost += other.lost
        self.repeated += other.repeated
        self.avoided += other.avoided

    @property
    def precision(self) -> Optional[float]:
        proposed = self.kept + self.repeated
        return self.kept / proposed if proposed else None


@dataclass
class Result:
    case: Labeled
    proposed: Dict[str, Set[str]] = field(default_factory=dict)     # arm -> recipients
    scores: Dict[str, Score] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)


def load_labeled(store, limit: Optional[int] = None, only: Optional[int] = None) -> List[Labeled]:
    """The decisions with a stored prompt and at least one labeled, offered route; newest first."""
    rows = store.query(
        "SELECT d.id, d.prompt, r.to_agent, r.label, r.labeled_at FROM decisions d "
        "JOIN routes r ON r.decision_id = d.id "
        "WHERE r.label IS NOT NULL AND r.verdict != 'drop' AND d.prompt != '' "
        + ("AND d.id = ? " if only is not None else "")
        + "ORDER BY d.id DESC, r.id ASC", [only] if only is not None else [])
    cases: Dict[int, Labeled] = {}
    for row in rows:
        case = cases.setdefault(row["id"], Labeled(row["id"], row["prompt"], row["labeled_at"] or 0.0, {}))
        case.labels[row["to_agent"]] = row["label"]
        case.labeled_at = max(case.labeled_at, row["labeled_at"] or 0.0)
    labeled = list(cases.values())
    return labeled[:limit] if limit else labeled


def score(labels: Dict[str, str], proposed: Set[str]) -> Score:
    result = Score()
    for recipient, label in labels.items():
        again = recipient in proposed
        if label == "good":
            result.kept += again
            result.lost += not again
        else:
            result.repeated += again
            result.avoided += not again
    return result


def memory_changed_at(agent_id: str = SWITCHBOARD) -> float:
    """When main's MEMORY or FEEDBACK last changed: what the dream last wrote."""
    from core.knowledge.memory import store
    from core.knowledge.memory.entries import FEEDBACK, PRIVATE

    times = [os.path.getmtime(p) for p in (store.private_scope(agent_id, PRIVATE).path,
                                           store.private_scope(agent_id, FEEDBACK).path) if os.path.exists(p)]
    return max(times, default=0.0)


@contextlib.contextmanager
def memory_off(agent_id: str = SWITCHBOARD):
    """`agent_id`'s MEMORY and FEEDBACK blocks rendered empty, for this process, within the block."""
    from core.knowledge.memory import inject

    render_memory, render_feedback = inject.render_memory, inject.render_feedback
    inject.render_memory = lambda agent, *a, **k: "" if agent == agent_id else render_memory(agent, *a, **k)
    inject.render_feedback = lambda agent, *a, **k: "" if agent == agent_id else render_feedback(agent, *a, **k)
    try:
        yield
    finally:
        inject.render_memory, inject.render_feedback = render_memory, render_feedback


async def replay(cases: List[Labeled], run: Callable, parse: Callable) -> List[Result]:
    """Each case's prompt through `run`, once per arm; `parse` reads the reply's proposals."""
    results = []
    for case in cases:
        result = Result(case)
        for arm in ARMS:
            with memory_off() if arm == "without" else contextlib.nullcontext():
                model_run = await run(case.prompt)
            if model_run.error:
                result.errors[arm] = model_run.error
                proposed: Set[str] = set()
            else:
                signals, problems = parse(model_run.reply)
                proposed = {route.to for signal in signals for route in signal.routes}
                if problems:
                    result.errors[arm] = "; ".join(problems)
            result.proposed[arm] = proposed
            result.scores[arm] = score(case.labels, proposed)
        print(f"[proactive-replay] #{case.decision_id}: with {sorted(result.proposed['with'])}, "
              f"without {sorted(result.proposed['without'])}", file=sys.stderr)
        results.append(result)
    return results


def totals(results: List[Result], since: float = 0.0) -> Dict[str, Score]:
    out = {arm: Score() for arm in ARMS}
    for result in results:
        if result.case.labeled_at > since:
            for arm in ARMS:
                out[arm].add(result.scores[arm])
    return out


def render(results: List[Result], memory_at: float) -> str:
    def row(name: str, scores: Dict[str, Score], count: int) -> str:
        cells = []
        for arm in ARMS:
            s = scores[arm]
            precision = f"{s.precision:.2f}" if s.precision is not None else "-"
            cells.append(f"{precision} ({s.kept} kept, {s.lost} lost, {s.repeated} repeated, {s.avoided} avoided)")
        return f"| {name} | {count} | {cells[0]} | {cells[1]} |"

    unseen = [r for r in results if r.case.labeled_at > memory_at]
    out = ["| Decisions | Count | With memory | Without memory |", "|---|---|---|---|",
           row("all labeled (flattered: memory learned from them)", totals(results), len(results)),
           row("labeled since main's memory last changed (fair)", totals(results, memory_at), len(unseen)), "",
           "| # | Your labels | With | Without | Problems |", "|---|---|---|---|---|"]
    for r in results:
        labels = ", ".join(f"{to} {'✅' if label == 'good' else '❌'}" for to, label in r.case.labels.items())
        problems = "; ".join(f"{arm}: {error}" for arm, error in r.errors.items())
        out.append(f"| {r.case.decision_id} | {labels} | {', '.join(sorted(r.proposed['with'])) or '-'} | "
                   f"{', '.join(sorted(r.proposed['without'])) or '-'} | {problems} |")
    return "\n".join(out)


async def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--limit", type=int, default=None, help="Replay at most this many decisions, newest first.")
    parser.add_argument("--decision", type=int, default=None, help="Replay only this decision.")
    parser.add_argument("--state-dir", default=None, help="Where the throwaway session DB goes.")
    parser.add_argument("--out", help="Write the report to this path too.")
    args = parser.parse_args(argv)

    from core.storage.signals import SignalStore

    cases = load_labeled(SignalStore(), args.limit, args.decision)  # before the session DB is swapped out
    memory_at = memory_changed_at()
    if not cases:
        print("No labeled decisions with a stored prompt yet: label some on team radio first.")
        return 1

    from scripts.voice_dryrun import guard_writes, isolate_sessions
    from core.runtime import bootstrap

    isolate_sessions(args.state_dir or tempfile.mkdtemp(prefix="aoc-proactive-replay-"))
    bootstrap.wire()
    guard_writes()

    from core.proactive.switchboard import parse_reply, run_model

    results = await replay(cases, run_model, parse_reply)
    report = render(results, memory_at)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
