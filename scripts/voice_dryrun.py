#!/usr/bin/env python3
"""Voice replay benchmark: replays voice turns through the real agents and
reports a per-stage latency table plus a quality pass/fail for every case.

Each case is an utterance in a room with an expected outcome
(scripts/voice_replay_cases.json). A run takes the path a Discord voice turn
takes, minus Discord itself: optional STT, the room's voice bot streamed on a
voice-surface session (so thinking levels, routing and delegation are the
production ones), the speech chunker, and TTS of the first chunk. Every case is
traced with the production TurnTrace, so the stage names match the
`[TurnTrace]` lines live turns log -- and checking quality on the same run keeps
a latency win from hiding a worse answer.

Case shape:

    {"id": "lights-status", "room": "home-automation",
     "utterance": "Are any lights on downstairs?",
     "expect": {"agent": "home-steward",            # who must answer
                "tools_any": ["home_assistant"],     # at least one of these
                "tools_all": [], "tools_none": [],   # every one / none of these
                "reply_matches": ["light"],          # regexes (case-insensitive)
                "reply_excludes": [],
                "spoken_excludes": ["Butler:"],      # never read aloud
                "max_model_calls": 3},               # round-trip budget
     "mutates": false}

Safety: cases run real tools against real data, and an agent may write even
when asked a question (meal-planner logs the dinner ideas it proposes). So by
default the replay is read-only: tool actions outside each tool's read set are
denied at the permission check, gog commands with a write verb are refused, and
every blocked write is listed in the report -- a write on a read question is a
round trip worth knowing about. --allow-mutations lifts the guard and also runs
the cases marked `"mutates": true`.
Conversation state is isolated: the run points every session store at a
throwaway database (delegated agents otherwise share production's `tool`
sessions), starts each case with no history, and uses record_memory=False, so a
replay never writes an agent memory log. Traces are logged, not stored.

Without Discord there is no playback, so the reported time to first audio is
`tts_first_byte`: the moment the first chunk's audio is ready to play.

Usage:
    python scripts/voice_dryrun.py                        # every read-only case, once
    python scripts/voice_dryrun.py --case lights-status --runs 3
    python scripts/voice_dryrun.py --stt live             # speak it into the VAD and streaming STT first
    python scripts/voice_dryrun.py --out results.json
"""
import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CASES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_replay_cases.json")
REPLAY_SOURCE = "voice_replay"

# Stages a replay can measure, in pipeline order. There is no playback
# (audio_start); vad_final and stt_done appear only with --stt live, when the
# utterance is spoken into the VAD instead of given as text.
REPLAY_STAGES = ("vad_final", "stt_done", "agent_start", "first_token", "first_chunk", "tts_first_byte", "turn_end")
# The voice --stt live speaks each utterance in (Chirp 3: HD).
LIVE_STT_VOICE = "en-US-Chirp3-HD-Charon"

EXPECT_KEYS = {
    "agent", "tools_any", "tools_all", "tools_none",
    "reply_matches", "reply_excludes", "spoken_excludes", "max_model_calls",
}

# Writes the read-only guard refused during the current case: (tool, action, target).
BLOCKED_WRITES: List[tuple] = []

# gog takes a free-form command line; these words mark one that changes something.
_GOG_WRITE_WORDS = {
    "create", "add", "send", "delete", "update", "insert", "move", "modify", "trash",
    "reply", "forward", "respond", "accept", "decline", "patch", "import", "label",
}


def read_only_actions() -> Dict[str, set]:
    """Per tool, the actions a read-only replay allows; anything else is refused.

    Taken from each tool's own read bundle, so a new read action needs no change
    here. Tools absent from this map (yfinance, seats_aero, zillow_query, ...)
    have no write actions to guard.
    """
    from tools import filesystem, google_health, home_assistant

    return {
        "filesystem": set(filesystem.PERMISSION_BUNDLES["@read"]),
        "home_assistant": set(home_assistant.PERMISSION_BUNDLES["@observe"]),
        "google_health": set(google_health.READ_ACTIONS),
        "bash": set(),
    }


class _ReadOnlySubprocess:
    """Stands in for `subprocess` inside tools/gog.py and refuses write commands."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def run(self, cmd, *args, **kwargs):
        words = {str(word).lower() for word in cmd[1:]}
        if words & _GOG_WRITE_WORDS:
            BLOCKED_WRITES.append(("gog", " ".join(str(w) for w in cmd[1:]), None))
            return self._real.CompletedProcess(cmd, 1, stdout="", stderr="Refused: the voice replay is read-only.")
        return self._real.run(cmd, *args, **kwargs)


def guard_writes() -> None:
    """Makes this process's tools read-only (see the module docstring)."""
    import subprocess as real_subprocess

    import tools.gog
    from core.loaders.tools_loader import ToolsLoader

    allowed_by_tool = read_only_actions()
    original = ToolsLoader.check_permission

    def check_permission(self, ctx, tool_id, action_name=None, path=None, **kwargs):
        allowed = allowed_by_tool.get(tool_id)
        if allowed is not None and action_name is not None and action_name not in allowed:
            BLOCKED_WRITES.append((tool_id, action_name, path))
            print(f"[replay] refused {tool_id}.{action_name} on {path} (read-only replay)", file=sys.stderr)
            return False
        return original(self, ctx, tool_id, action_name, path, **kwargs)

    ToolsLoader.check_permission = check_permission
    tools.gog.subprocess = _ReadOnlySubprocess(real_subprocess)


@dataclass
class CaseResult:
    case_id: str
    run: int
    marks: Dict[str, int]
    reply: str = ""
    transcript: str = ""
    spoken: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    agents: List[str] = field(default_factory=list)
    model_calls: List[dict] = field(default_factory=list)
    tokens: Dict[str, int] = field(default_factory=dict)
    blocked_writes: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def load_cases(path: str = CASES_PATH, only: Optional[List[str]] = None, allow_mutations: bool = False) -> List[dict]:
    """The cases to run. Unknown ids and unknown expectation keys are errors, not silent no-ops."""
    with open(path, encoding="utf-8") as f:
        cases = json.load(f)
    by_id = {c["id"]: c for c in cases}
    for case in cases:
        unknown = set(case.get("expect", {})) - EXPECT_KEYS
        if unknown:
            raise ValueError(f"case {case['id']}: unknown expectation(s) {sorted(unknown)}")
    if only:
        missing = [c for c in only if c not in by_id]
        if missing:
            raise ValueError(f"unknown case id(s): {missing}")
        cases = [by_id[c] for c in only]
    if not allow_mutations:
        cases = [c for c in cases if not c.get("mutates")]
    return cases


def check_expectations(case: dict, result: CaseResult) -> List[str]:
    """Every way `result` falls short of the case's expected outcome."""
    expect = case.get("expect", {})
    failures = []
    if "agent" in expect and expect["agent"] not in result.agents:
        failures.append(f"answered by {result.agents or ['nobody']}, expected {expect['agent']}")
    if expect.get("tools_any") and not set(expect["tools_any"]) & set(result.tools):
        failures.append(f"none of {expect['tools_any']} was called (called: {result.tools or 'none'})")
    for tool in expect.get("tools_all", []):
        if tool not in result.tools:
            failures.append(f"{tool} was not called")
    for tool in expect.get("tools_none", []):
        if tool in result.tools:
            failures.append(f"{tool} was called")
    for pattern in expect.get("reply_matches", []):
        if not re.search(pattern, result.reply, re.IGNORECASE):
            failures.append(f"reply lacks /{pattern}/")
    for pattern in expect.get("reply_excludes", []):
        if re.search(pattern, result.reply, re.IGNORECASE):
            failures.append(f"reply contains /{pattern}/")
    spoken = " ".join(result.spoken)
    for pattern in expect.get("spoken_excludes", []):
        if re.search(pattern, spoken, re.IGNORECASE):
            failures.append(f"speech contains /{pattern}/")
    if "max_model_calls" in expect and len(result.model_calls) > expect["max_model_calls"]:
        failures.append(f"{len(result.model_calls)} model calls, budget {expect['max_model_calls']}")
    return failures


def percentile(values: List[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * pct // 100))  # ceil(n * pct / 100), at least 1
    return ordered[int(rank) - 1]


def stage_table(results: List[CaseResult]) -> List[tuple]:
    """(stage, p50 ms, p90 ms, samples) for every stage a replay measures."""
    rows = []
    for stage in REPLAY_STAGES:
        values = [r.marks[stage] for r in results if stage in r.marks]
        if values:
            rows.append((stage, percentile(values, 50), percentile(values, 90), len(values)))
    return rows


def render_report(results: List[CaseResult]) -> str:
    lines = ["| Case | Run | Result | First token | First chunk | First audio ready | Model calls (thinking) | Tokens in/out | Notes |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        calls = ", ".join(str(c.get("thinking") or "default") for c in r.model_calls) or "-"
        lines.append(
            f"| {r.case_id} | {r.run} | {'PASS' if r.passed else 'FAIL'} "
            f"| {_ms(r.marks.get('first_token'))} | {_ms(r.marks.get('first_chunk'))} | {_ms(r.marks.get('tts_first_byte'))} "
            f"| {len(r.model_calls)} ({calls}) | {r.tokens.get('input', 0)}/{r.tokens.get('output', 0)} "
            f"| {'; '.join(r.failures + [f'refused write: {w}' for w in r.blocked_writes])} |"
        )
    lines += ["", "| Stage | p50 | p90 | n |", "|---|---|---|---|"]
    for stage, p50, p90, n in stage_table(results):
        lines.append(f"| {stage} | {_ms(p50)} | {_ms(p90)} | {n} |")
    passed = sum(r.passed for r in results)
    lines += ["", f"Quality: {passed}/{len(results)} runs passed."]
    return "\n".join(lines)


def _ms(value) -> str:
    return "-" if value is None else f"{value / 1000:.2f} s"


def isolate_sessions(directory: str) -> str:
    """Points every session store at `directory/memory.db` for this process.

    Conversation history, checkpoints, token rows and jobs all default to the
    production sessions/memory.db. A replay must not read the real channel's
    history (runs wouldn't be comparable) or write into it -- delegated agents
    run on `<agent>:tool:<channel>` sessions that live turns share. This is the
    same redirect the test suite applies in tests/conftest.py.
    """
    from core.knowledge.memory import sqlite_checkpointer, sqlite_session_store
    from core.runtime import job_manager

    os.makedirs(directory, exist_ok=True)
    db_path = os.path.join(directory, "memory.db")
    for module in (job_manager, sqlite_session_store, sqlite_checkpointer):
        module.SESSIONS_DIR = directory
        module.DEFAULT_DB_PATH = db_path
    sqlite_session_store.SqliteSessionStore.__init__.__defaults__ = (db_path,)
    sqlite_checkpointer.SqliteCheckpointer.__init__.__defaults__ = (db_path,)
    job_manager.JobManager._instance = None
    return db_path


def reset_history(db_path: str) -> None:
    """Deletes every checkpoint in the isolated DB, so the next case starts with no history."""
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ctx_%'")]
        for table in tables:
            conn.execute(f"DELETE FROM \"{table}\" WHERE entry_type IN ('checkpoint', 'write')")
        conn.commit()
    finally:
        conn.close()


def replay_tokens(since: float, db_path: str) -> Dict[str, int]:
    """Input/output tokens every session wrote since `since` (epoch seconds) in the isolated DB."""
    totals = {"input": 0, "output": 0}
    if not os.path.exists(db_path):
        return totals
    conn = sqlite3.connect(db_path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ctx_%'")]
        for table in tables:
            row = conn.execute(
                f'SELECT COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0) FROM "{table}" '
                "WHERE entry_type = 'token' AND created_at >= ?", (since,)
            ).fetchone()
            totals["input"] += row[0]
            totals["output"] += row[1]
    finally:
        conn.close()
    return totals


@dataclass
class HeardSpeech:
    """An utterance as the listening path heard it (time.monotonic() stamps)."""
    transcript: str
    speech_end_at: Optional[float]
    vad_final_at: float
    stt_done_at: float


def live_hearing(voice: str = LIVE_STT_VOICE):
    """Returns `hear(text)`: speaks `text` with Chirp 3: HD into the real VAD and
    streaming recognizer, in real time, as a voice turn would hear it (the
    machinery of scripts/stt_benchmark.py). Needs GOOGLE_TTS_API_KEY and
    GEMINI_API_KEY."""
    import stt_benchmark as bench
    from core.loaders.agents_loader import AgentsLoader
    from core.util.config import Config
    from core.voice.engines.stt import SpeechRecognizer, agent_vocabulary
    from core.voice.turn_taking import merge_transcripts
    from tts_benchmark import API_KEY_ENV_VAR, Options, make_chirp_client

    client, tts_module = make_chirp_client(Options(tts_api_key=(Config().get(API_KEY_ENV_VAR) or "").strip()))
    recognizer = SpeechRecognizer(provider="gemini_live", vocabulary=agent_vocabulary(AgentsLoader()))

    async def hear(text: str) -> Optional[HeardSpeech]:
        pcm = await asyncio.to_thread(bench.synthesize, client, tts_module, text, voice)
        heard = [h for h in await bench.listen([pcm], 0, recognizer) if h.stt_done_at is not None]
        if not heard:
            return None
        last = heard[-1]
        return HeardSpeech(merge_transcripts(h.text for h in heard), last.utterance.speech_end_at,
                           last.vad_final_at, last.stt_done_at)

    return hear


async def run_case(case: dict, run: int, db_path: str, voice_bot: str = "main", hear=None, tts=None,
                   measure_tts: bool = True) -> CaseResult:
    """Replays one case and returns what happened.

    `hear` set (see `live_hearing`): the utterance is spoken into the VAD and
    streaming STT first, and the trace is anchored at the last voiced frame,
    as in a live turn. `measure_tts`: synthesize the first chunk to time it.
    """
    from core.loaders.agents_loader import AgentsLoader
    from core.runtime.session_manager import SessionManager
    from core.runtime.turn_trace import start_turn_trace
    from core.voice.speech.verbalizer import Verbalizer
    from core.voice.speech.voice_stream import VoiceStream

    reset_history(db_path)
    BLOCKED_WRITES.clear()
    heard = await hear(case["utterance"]) if hear is not None else None
    result = CaseResult(case_id=case["id"], run=run, marks={})
    if hear is not None and heard is None:
        result.transcript = ""
        result.failures.append("STT heard nothing")
        return result

    started = time.time()
    trace = start_turn_trace(voice_bot, run, speech_end_at=heard.speech_end_at if heard else None, case=case["id"])
    if heard is not None:
        result.transcript = heard.transcript
        trace.mark("vad_final", at=heard.vad_final_at)
        trace.mark("stt_done", at=heard.stt_done_at)
    else:
        result.transcript = case["utterance"]

    session = SessionManager.get_session(
        agent_id=voice_bot, source=REPLAY_SOURCE, channel=case["room"], surface="voice", record_memory=False,
    )
    agent = AgentsLoader().get_agent(voice_bot)
    voice_stream = VoiceStream(verbalizer=Verbalizer(), session=session)
    tts_task = None

    def speak(chunks):
        nonlocal tts_task
        for chunk in chunks:
            trace.mark("first_chunk")
            result.spoken.append(chunk)
            if tts_task is None and measure_tts and tts is not None:
                # Synthesized beside the stream, as the voice pipeline's speech lane does.
                async def first_audio(text=chunk):
                    if await tts.synthesize_to_file(text):
                        trace.mark("tts_first_byte")
                tts_task = asyncio.create_task(first_audio())

    trace.mark("agent_start")
    async for event in agent.execute_stream(result.transcript, session=session):
        kind = event.get("type")
        if kind == "token" and event.get("content"):
            result.agents = sorted(set(result.agents) | {event.get("agent_id") or voice_bot})
            if not event.get("is_header"):
                trace.mark("first_token")
                speak(await voice_stream.add_token(event["content"]))
        elif kind == "tool_start":
            result.tools.append(event.get("tool_name", ""))
            trace.event("tool_start", tool=event.get("tool_name", ""))
        elif kind == "tool_end":
            trace.event("tool_end", tool=event.get("tool_name", ""))
        elif kind == "final_response":
            result.reply = event.get("text", "") or ""
        elif kind == "error":
            result.failures.append(f"agent error: {event.get('content')}")
    speak(await voice_stream.flush())
    if tts_task is not None:
        await tts_task

    summary = trace.finish(session_id=None)
    result.marks = summary["marks"]
    result.model_calls = [e for e in summary["events"] if e["kind"] == "model_call"]
    result.tokens = replay_tokens(started, db_path)
    result.blocked_writes = [f"{tool}.{action} {target or ''}".strip() for tool, action, target in BLOCKED_WRITES]
    result.failures += check_expectations(case, result)
    return result


async def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Replay voice turns and report latency and quality.")
    parser.add_argument("--cases", default=CASES_PATH, help="Case file (JSON list).")
    parser.add_argument("--case", action="append", dest="only", help="Run only this case id (repeatable).")
    parser.add_argument("--runs", type=int, default=1, help="Runs per case, for p50/p90.")
    parser.add_argument("--stt", choices=("off", "live"), default="off",
                        help="live: speak each utterance into the VAD and streaming STT, to include them.")
    parser.add_argument("--no-tts", action="store_true", help="Don't synthesize the first chunk.")
    parser.add_argument("--allow-mutations", action="store_true", help="Also run cases marked mutates=true.")
    parser.add_argument("--voice-bot", default="main", help="The bot that hosts the rooms (default: main).")
    parser.add_argument("--state-dir", default=None,
                        help="Where the throwaway session DB goes (default: a new temp directory).")
    parser.add_argument("--out", help="Write results as JSON to this path.")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases, args.only, args.allow_mutations)
    if not cases:
        print("No cases to run.")
        return 1

    import tempfile
    db_path = isolate_sessions(args.state_dir or tempfile.mkdtemp(prefix="aoc-voice-replay-"))

    from core.runtime.execution_context import surface_scope
    from core.voice.engines.tts_engine import TTSEngine

    tts = TTSEngine(default_voice="en-US-JennyNeural") if not args.no_tts else None
    hear = live_hearing() if args.stt == "live" else None

    if not args.allow_mutations:
        guard_writes()

    results = []
    with surface_scope("voice"):
        for case in cases:
            for run in range(1, args.runs + 1):
                print(f"[replay] {case['id']} run {run}: {case['utterance']!r}", file=sys.stderr)
                results.append(await run_case(case, run, db_path, args.voice_bot, hear=hear, tts=tts,
                                              measure_tts=not args.no_tts))

    print(render_report(results))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump([{**asdict(r), "passed": r.passed} for r in results], f, indent=2)
    return 0 if all(r.passed for r in results) else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
