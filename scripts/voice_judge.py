#!/usr/bin/env python3
"""Judges a voice benchmark that voice_dryrun.py ran: for every case and run,
PRO compares each combo's reply with the reference combo's, and the report
holds each combo to the light model's bar. From Discord:

    !python voice_judge.py sessions/voice_bench/results.json           submit, or collect once done
    !python voice_judge.py sessions/voice_bench/results.json --sync    ask now: for a handful of pairs

The results are `voice_dryrun.py --out`'s, from runs on several combos
(--combo, --matrix). Each combo is compared with the reference: PRO:low by
default, PRO at the voice default level, since the bar is set against PRO;
--reference picks another, `prod` (production as configured) among them.

Pairwise, not scored: the judge sees the user's words and both replies --
what was said aloud, what the channel showed, who answered and with which
tools -- and says which serves the user better, or that it's a tie, with a
one-line reason. Which reply is shown first is drawn per pair from --seed,
so a lean toward either position cancels out over the pairs, and a rerun
draws the same. Some pairs need no judge:
- a run that failed loses to one that didn't;
- identical replies tie;
- a case no model answered on either side (the fixed line) isn't counted,
  since it says nothing about the model, nor is a pair whose reference run
  failed.

The Batch API answers at half the price, on a quota apart from the live
bot's, within minutes to hours. So the first run submits the unjudged pairs
and keeps the job's name in a state file beside the results
(<results>.judge.json), and a rerun collects the verdicts once the job is
done instead of submitting again. It submits only pairs still unjudged:
runs added since, or ones whose replies changed. --sync asks directly
instead, one pair at a time, waiting out a 429.

The bar: a combo passes when its reply wins or ties the reference's in at
least 72% of the judged pairs, and its first speech token arrives within
700 ms at p50. 72% is what PRO:medium, at least as good as PRO:low, scored
against it: run-to-run variation alone loses about a quarter of the pairs,
so a higher bar would fail a combo as good as the reference. To ask what a
change does, judge it against the same setup without it: `--reference prod`
for runs with the voice graph's light first call (`prod+light:...`).
First speech is `first_token`, the turn's
first model text (replies open with <speech>, so it's what the voice starts
on), timed from the start of the turn's first model call: what the model
costs, not the runtime before it, which is the same for every combo and
what the voice graph cuts. `first_chunk`, the first piece handed to TTS,
and the first audio (`tts_first_byte`) are timed from the same start. The
first call's first chunk is that call's own latency, whatever it streamed
first: text or a tool call.
"""
import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.config.models import PRO  # noqa: E402
from scripts.voice_dryrun import (  # noqa: E402
    PROD, RATE_LIMIT_RE, RATE_LIMIT_WAITS, REFERENCE, load_results, percentile, retry_wait, save_json)

# `!python` runs this without `--yes` (core/runtime/commands/typed/python.py):
# it writes nothing but its own state file, beside the results.
READ_ONLY = True

JUDGE_MODEL = PRO
WIN_OR_TIE_BAR = 0.72     # of the judged pairs: what PRO:medium scored against PRO:low
FIRST_TOKEN_BAR_MS = 700  # p50, from the start of the turn's first model call

# Tools the runtime runs itself, not the agent: left out of the tools the judge
# sees, since they'd only tell it which setup a reply came from. `handover` is
# the voice graph's light first call handing the turn over
# (core/runtime/model/handover.py `HANDOVER`).
RUNTIME_TOOLS = ("handover",)

JUDGE_INSTRUCTIONS = """You compare two replies of a voice assistant to the same spoken request.
The user heard what each reply said aloud; the rest went to their chat channel as text. Both
replies came from the same assistant, with the same tools and data, minutes apart.

Decide which reply serves the user better, in this order:
1. Correct and on point: it answers what was asked, consistently with what its tools would have
   shown; it invents nothing and doesn't contradict itself.
2. Heard well: what was said aloud is clear, natural to listen to and enough to act on, and it
   leaves long details to the channel.
3. The channel text: accurate, and useful beyond the speech.

A reply may open with one short line said at once, while the rest was still being worked out
("Let me check the lights."). Judge that line only on whether it's accurate, not as filler.

Length isn't quality, and the order the replies are shown in means nothing. When neither is
clearly better, it's a tie: small differences in wording are a tie.

Answer in JSON: {"verdict": "A", "B" or "tie", "reason": one line of at most 25 words}."""

VERDICT_SCHEMA = {
    "type": "OBJECT",
    "properties": {"verdict": {"type": "STRING", "enum": ["A", "B", "tie"]},
                   "reason": {"type": "STRING"}},
    "required": ["verdict", "reason"],
}

# Batch job states (google.genai.types.JobState): done, or ended without verdicts.
DONE_STATES = ("JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED")
FAILED_STATES = ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED")

_sleep = time.sleep  # indirection so tests can skip the waits


@dataclass
class Pair:
    """One combo's run of a case, beside the reference's run of the same case."""
    key: str                  # "lights-status#1 FLASH:low vs PRO:low"
    combo: str
    combo_first: bool = True  # the combo's reply is shown as A
    prompt: str = ""          # the question for the judge, when one is needed
    outcome: str = ""         # win, tie or loss, when it's decided without the judge
    reason: str = ""
    skipped: str = ""         # why the pair isn't counted

    @property
    def fingerprint(self) -> str:
        """Ties a verdict to the replies it was given: changed replies get a new one."""
        return hashlib.sha256(self.prompt.encode()).hexdigest()[:16]


def make_pairs(results: List[dict], reference: str, seed: int = 0) -> List[Pair]:
    """Each combo's run of a case beside the reference's same run, in the results' order."""
    runs = {(r["case_id"], r["run"], r.get("combo", PROD)): r for r in results}
    pairs = []
    for (case_id, run, combo), mine in runs.items():
        theirs = runs.get((case_id, run, reference))
        if combo == reference or theirs is None:
            continue
        pair = Pair(f"{case_id}#{run} {combo} vs {reference}", combo)
        if theirs.get("errored"):
            pair.skipped = "the reference run failed"
        elif not mine.get("model_calls") and not theirs.get("model_calls"):
            pair.skipped = "no model answered"
        elif mine.get("errored"):
            failure = next(iter(mine.get("failures") or []), "it failed")
            pair.outcome, pair.reason = "loss", f"its run failed: {failure}"
        elif (_spoken(mine), _text(mine)) == (_spoken(theirs), _text(theirs)):
            pair.outcome, pair.reason = "tie", "the same reply"
        else:
            pair.combo_first = random.Random(f"{seed}:{pair.key}").random() < 0.5
            first, second = (mine, theirs) if pair.combo_first else (theirs, mine)
            pair.prompt = judge_prompt(theirs, first, second)
        pairs.append(pair)
    return pairs


def judge_prompt(request: dict, first: dict, second: dict) -> str:
    """The question about one pair: what the user said, then reply A and reply B."""
    said = request.get("transcript") or ""
    return "\n\n".join([f"Room: {request.get('room') or 'unknown'}", f'The user said: "{said}"',
                        _reply("A", first, said), _reply("B", second, said)])


def _reply(name: str, run: dict, said: str) -> str:
    lines = [f'<reply id="{name}">']
    if run.get("transcript") and run["transcript"] != said:  # with --stt live, each run heard for itself
        lines.append(f'Heard as: "{run["transcript"]}"')
    tools = [tool for tool in dict.fromkeys(run.get("tools") or []) if tool not in RUNTIME_TOOLS]
    lines += [f"Answered by: {', '.join(run.get('agents') or []) or 'nobody'}",
              f"Tools used: {', '.join(tools) or 'none'}",
              "Said aloud:", _spoken(run) or "(nothing)",
              "Posted in the channel:", _text(run) or "(nothing)",
              "</reply>"]
    return "\n".join(lines)


def _spoken(run: dict) -> str:
    """What the run said aloud: its speech, or the fallback or error line it spoke instead."""
    return " ".join(chunk.strip() for chunk in run.get("spoken") or []).strip()


def _text(run: dict) -> str:
    return (run.get("reply") or "").strip()


VERDICTS = {"a": "A", "b": "B", "tie": "tie"}


def parse_verdict(text: str) -> Tuple[str, str]:
    """("A", "B" or "tie", the reason) from the judge's JSON; ValueError for anything else."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        data = None
    verdict = VERDICTS.get(str(data.get("verdict", "")).strip().lower()) if isinstance(data, dict) else None
    if verdict is None:
        raise ValueError(f"not a verdict: {(text or '')[:200]!r}")
    return verdict, " ".join(str(data.get("reason", "")).split())


def outcome(verdict: str, combo_first: bool) -> str:
    """The judge's A/B/tie, as the combo's win, tie or loss."""
    if verdict == "tie":
        return "tie"
    return "win" if (verdict == "A") == combo_first else "loss"


# --- the state file: batch jobs and verdicts --------------------------------------

def load_state(path: str) -> dict:
    """{"batches": [...], "verdicts": {pair key: ...}}; empty without the file."""
    state = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    state.setdefault("batches", [])
    state.setdefault("verdicts", {})
    return state


def verdict_of(pair: Pair, state: dict) -> Optional[Tuple[str, str]]:
    """(win/tie/loss, reason) for `pair`, or None while it's unjudged."""
    if pair.outcome:
        return pair.outcome, pair.reason
    stored = state["verdicts"].get(pair.key)
    if stored and stored.get("fingerprint") == pair.fingerprint:
        return stored["outcome"], stored.get("reason", "")
    return None


def record(state: dict, pair: Pair, text: str) -> None:
    """Stores the judge's answer `text` for `pair`; ValueError when it isn't a verdict."""
    verdict, reason = parse_verdict(text)
    state["verdicts"][pair.key] = {"outcome": outcome(verdict, pair.combo_first), "verdict": verdict,
                                   "combo_was": "A" if pair.combo_first else "B", "reason": reason,
                                   "fingerprint": pair.fingerprint}


def in_flight(state: dict) -> Dict[str, str]:
    """Pair key -> the open batch job asking about it."""
    return {key: batch["name"] for batch in state["batches"] if not batch.get("done") for key in batch["pairs"]}


# --- asking the judge ---------------------------------------------------------------

def make_client():
    """A Gemini client on the deployment's key."""
    from google import genai
    from core.config.settings import Config

    key = (Config().gemini_api_key or "").strip()
    if not key:
        raise SystemExit("GEMINI_API_KEY isn't set.")
    return genai.Client(api_key=key)


def judge_config() -> dict:
    return {"system_instruction": JUDGE_INSTRUCTIONS, "response_mime_type": "application/json",
            "response_schema": VERDICT_SCHEMA}


def ask(client, prompt: str) -> str:
    """The judge's answer about one pair, asked directly; a 429 is waited out as the replay does."""
    attempt = 0
    while True:
        try:
            return client.models.generate_content(model=JUDGE_MODEL, contents=prompt, config=judge_config()).text or ""
        except Exception as e:
            if attempt >= len(RATE_LIMIT_WAITS) or not RATE_LIMIT_RE.search(str(e)):
                raise
            wait = retry_wait(str(e), attempt)
            print(f"[judge] rate-limited; trying again in {wait:.0f} s", file=sys.stderr)
            _sleep(wait)
            attempt += 1


def judge_now(client, pairs: List[Pair], state: dict, save: Callable[[], None]) -> None:
    """Asks about each pair directly, one at a time, saving after each verdict."""
    for i, pair in enumerate(pairs, 1):
        print(f"[judge] {i}/{len(pairs)}: {pair.key}", file=sys.stderr)
        try:
            record(state, pair, ask(client, pair.prompt))
        except ValueError as e:
            print(f"[judge] {pair.key}: {e}", file=sys.stderr)
            continue
        save()


def submit(client, pairs: List[Pair], state: dict, label: str) -> dict:
    """Submits `pairs` as one batch job and keeps its name, so a rerun collects it."""
    job = client.batches.create(
        model=JUDGE_MODEL,
        src=[{"contents": [{"role": "user", "parts": [{"text": pair.prompt}]}], "config": judge_config(),
              "metadata": {"key": pair.key}} for pair in pairs],
        config={"display_name": f"voice-judge {label}"[:100]},
    )
    batch = {"name": job.name, "state": _state_name(job), "done": False,
             "submitted": time.strftime("%Y-%m-%d %H:%M:%S"), "pairs": {pair.key: pair.fingerprint for pair in pairs}}
    state["batches"].append(batch)
    return batch


def collect(client, state: dict, pairs: Dict[str, Pair]) -> None:
    """Checks each open batch job: takes a finished one's verdicts, and closes one
    that ended without them, so its pairs are asked about again."""
    for batch in state["batches"]:
        if batch.get("done"):
            continue
        job = client.batches.get(name=batch["name"])
        batch["state"] = _state_name(job)
        responses = list(getattr(getattr(job, "dest", None), "inlined_responses", None) or [])
        if batch["state"] in FAILED_STATES:
            print(f"[judge] {batch['name']} ended {batch['state']}: its pairs go in a new job", file=sys.stderr)
            batch["done"] = True
        elif batch["state"] in DONE_STATES and not responses:
            # Kept open rather than asked again, which would pay for it on every rerun.
            print(f"[judge] {batch['name']} is done but returned no answers inline: left open", file=sys.stderr)
        elif batch["state"] in DONE_STATES:
            _take_verdicts(responses, batch, state, pairs)
            batch["done"] = True


def _take_verdicts(responses: list, batch: dict, state: dict, pairs: Dict[str, Pair]) -> None:
    keys = list(batch["pairs"])
    for i, response in enumerate(responses):
        # By the key in its metadata, else by place: they come in the requests' order.
        key = (getattr(response, "metadata", None) or {}).get("key") or (keys[i] if i < len(keys) else None)
        pair = pairs.get(key)
        if pair is None or batch["pairs"].get(key) != pair.fingerprint:
            continue  # the replies changed since it was asked: it's asked again
        if getattr(response, "error", None) or getattr(response, "response", None) is None:
            print(f"[judge] {key}: no answer ({getattr(response, 'error', None)})", file=sys.stderr)
            continue
        try:
            record(state, pair, response.response.text)
        except ValueError as e:
            print(f"[judge] {key}: {e}", file=sys.stderr)


def _state_name(job) -> str:
    state = getattr(job, "state", None)
    return str(getattr(state, "name", state) or "JOB_STATE_UNSPECIFIED")


# --- the report ------------------------------------------------------------------------

def since_first_call(run: dict, stage: str) -> Optional[int]:
    """Milliseconds from the start of the turn's first model call to `stage`; None without either."""
    starts = [call["t"] for call in run.get("model_calls") or [] if isinstance(call.get("t"), (int, float))]
    mark = (run.get("marks") or {}).get(stage)
    if not starts or mark is None or mark < min(starts):
        return None
    return mark - min(starts)


def first_call_reply(run: dict) -> Optional[int]:
    """Milliseconds the turn's first model call took to stream its first chunk."""
    calls = run.get("model_calls") or []
    number = calls[0].get("call") if calls else None
    reply = next((e for e in run.get("model_replies") or []
                  if number is not None and e.get("call") == number and e.get("phase") == "first_chunk"), None)
    return None if reply is None else reply["t"] - calls[0]["t"]


def bar(win_or_tie: float, first_token_p50: Optional[float]) -> str:
    """PASS, or FAIL with what fell short."""
    misses = []
    if win_or_tie < WIN_OR_TIE_BAR:
        misses.append(f"wins or ties {win_or_tie:.1%}")  # a tenth, so 89.7% doesn't read as 90%
    if first_token_p50 is None:
        misses.append("no first token")
    elif first_token_p50 > FIRST_TOKEN_BAR_MS:
        misses.append(f"first token {first_token_p50:.0f} ms")
    return "FAIL: " + ", ".join(misses) if misses else "PASS"


def render(results: List[dict], pairs: List[Pair], state: dict, reference: str, combos: List[str]) -> str:
    lines = [f"Judge: {JUDGE_MODEL}, each combo against {reference}, pairwise. Latency is timed from the start of "
             f"each turn's first model call.", "",
             "| Combo | Judged | Win / tie / loss | Win or tie | First token p50 / p90 | First chunk p50 / p90 "
             "| First audio p50 | First call's first chunk p50 | Model calls p50 | Checks passed | Bar |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for combo in combos:
        runs = [r for r in results if r.get("combo", PROD) == combo]
        answered = [r for r in runs if r.get("model_calls")]  # the fixed line says nothing about the model
        token = _measured(answered, lambda r: since_first_call(r, "first_token"))
        chunk = _measured(answered, lambda r: since_first_call(r, "first_chunk"))
        audio = _measured(answered, lambda r: since_first_call(r, "tts_first_byte"))
        first_reply = _measured(answered, first_call_reply)
        timing = (f"| {_spread(token)} | {_spread(chunk)} | {_seconds(percentile(audio, 50))} "
                  f"| {_seconds(percentile(first_reply, 50))} "
                  f"| {_number(percentile([len(r['model_calls']) for r in answered], 50))} "
                  f"| {sum(bool(r.get('passed')) for r in runs)}/{len(runs)}")
        if combo == reference:
            lines.append(f"| {combo} (reference) | - | - | - {timing} | reference |")
            continue
        verdicts = [verdict_of(p, state) for p in pairs if p.combo == combo and not p.skipped]
        judged = [v[0] for v in verdicts if v]
        waiting = len(verdicts) - len(judged)
        wins, ties, losses = (judged.count(o) for o in ("win", "tie", "loss"))
        rate = (wins + ties) / len(judged) if judged else None
        verdict = "waiting" if waiting else bar(rate, percentile(token, 50)) if judged else "-"
        judged_cell = f"{len(judged)}" + (f" (+{waiting} waiting)" if waiting else "")
        rate_cell = "-" if rate is None else f"{rate:.0%}"
        lines.append(f"| {combo} | {judged_cell} | {wins} / {ties} / {losses} | {rate_cell} {timing} | {verdict} |")

    lines += ["", f"Bar: wins or ties {reference} in at least {WIN_OR_TIE_BAR:.0%} of the judged pairs, and the "
                  f"first token within {FIRST_TOKEN_BAR_MS} ms at p50."]
    skipped = [p.skipped for p in pairs if p.skipped]
    if skipped:
        lines.append("Not counted: " + ", ".join(f"{_pairs(skipped.count(why))} where {why}"
                                                 for why in dict.fromkeys(skipped)) + ".")
    for batch in state["batches"]:
        if not batch.get("done"):
            lines.append(f"Waiting on {batch['name']} ({batch['state']}, {_pairs(len(batch['pairs']))}): "
                         f"run this again to collect.")
    asked = in_flight(state)
    unasked = [p for p in pairs if p.prompt and verdict_of(p, state) is None and p.key not in asked]
    if unasked:
        lines.append(f"Not asked yet: {_pairs(len(unasked))}. Run this again to ask.")
    losses = [(p, v) for p in pairs for v in [verdict_of(p, state)] if v and v[0] == "loss"]
    if losses:
        lines += ["", "Losses:"]
        for pair, (_, reason) in losses:
            shown = f" (it was {'A' if pair.combo_first else 'B'})" if pair.prompt else ""
            lines.append(f"- {pair.key}{shown}: {reason}")
    return "\n".join(lines)


def _measured(runs: List[dict], measure: Callable[[dict], Optional[int]]) -> List[int]:
    return [value for value in map(measure, runs) if value is not None]


def _seconds(ms: Optional[float]) -> str:
    return "-" if ms is None else f"{ms / 1000:.2f} s"


def _spread(values: List[int]) -> str:
    """p50 / p90 in seconds."""
    if not values:
        return "-"
    return f"{percentile(values, 50) / 1000:.2f} / {percentile(values, 90) / 1000:.2f} s"


def _number(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:g}"


def _pairs(count: int) -> str:
    return f"{count} pair" + ("" if count == 1 else "s")


def main(argv: Optional[List[str]] = None, client=None) -> int:
    parser = argparse.ArgumentParser(description="Judge a voice benchmark's combos against a reference, pairwise.")
    parser.add_argument("results", help="The results file voice_dryrun.py --out wrote.")
    parser.add_argument("--reference", default=REFERENCE,
                        help=f"The combo every other one is compared with (default: {REFERENCE}; "
                             f"{PROD} is production as configured).")
    parser.add_argument("--seed", type=int, default=0, help="Draws which reply of each pair is shown first.")
    parser.add_argument("--sync", action="store_true",
                        help="Ask directly, one pair at a time, instead of through the Batch API.")
    parser.add_argument("--limit", type=int, default=0, help="Ask about at most this many new pairs now (0: all).")
    parser.add_argument("--state", help="Where batch jobs and verdicts are kept (default: <results>.judge.json).")
    args = parser.parse_args(argv)

    results = load_results(args.results)
    combos = list(dict.fromkeys(r.get("combo", PROD) for r in results))
    if args.reference not in combos:
        print(f"No runs on the reference, {args.reference}, in {args.results}: run it too, or pass --reference "
              f"with one of: {', '.join(combos) or 'none'}.")
        return 1
    pairs = make_pairs(results, args.reference, args.seed)
    by_key = {pair.key: pair for pair in pairs}
    state_path = args.state or os.path.splitext(args.results)[0] + ".judge.json"
    state = load_state(state_path)

    def save():
        save_json(state_path, state)

    if any(not batch.get("done") for batch in state["batches"]):
        client = client or make_client()
        collect(client, state, by_key)
        save()
    asked = in_flight(state)
    new = [p for p in pairs if p.prompt and verdict_of(p, state) is None and p.key not in asked]
    if args.limit > 0:
        new = new[:args.limit]
    if new:
        client = client or make_client()
        if args.sync:
            judge_now(client, new, state, save)
        else:
            batch = submit(client, new, state, os.path.basename(args.results))
            save()
            print(f"[judge] Submitted {len(new)} pairs as {batch['name']}.", file=sys.stderr)
    print(render(results, pairs, state, args.reference,
                 [args.reference] + [combo for combo in combos if combo != args.reference]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
