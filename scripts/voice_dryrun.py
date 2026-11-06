#!/usr/bin/env python3
"""Voice replay benchmark: replays voice turns through the real agents and
reports a per-stage latency table plus a quality pass/fail for every case.

Each case is an utterance in a room with an expected outcome
(scripts/voice_replay_cases.json). A run takes the path a Discord voice turn
takes, minus Discord itself: optional STT, the room's voice bot streamed on a
voice-surface session (so thinking levels, routing, delegation and
`<voice_mode>` are the production ones), the speech router and chunker, and
TTS of the first chunk. Every case is traced with the production TurnTrace, so
the stage names match the `[TurnTrace]` lines live turns log -- and checking
quality on the same run keeps a latency win from hiding a worse answer.

Every run is also held to the speech contract, whatever its case expects: the
reply came with a <speech> block (else the fallback was spoken, and the run
fails), no <speech> reached the text, the speech is in English (the voices
are), and it kept every number and named item of the text reply. Numbers are
matched exactly (in digits or words), so a dropped one fails the run unless
the speech says the rest is in the channel. Names can't be -- "day planning"
says "Day Planner" well enough -- so possible misses are notes for a person
to judge. A run that failed is held to none of it, as in production.

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
sessions), starts each case with no history (a live case with its own, below),
and uses record_memory=False, so a replay never writes an agent memory log.
Traces are logged, not stored. Nor is history pruned: each case is one turn,
and a prune runs after a turn, in the background, where it could rewrite the
next case's history.

Your own conversations: --live NAMES (agent ids or names, comma-separated:
--live Ted,Aki) makes the cases from each agent's conversation in
production's memory.db instead of the case file: the last --live-turns turns
you spoke there that it answered (`live_cases`). Each runs on the
conversation as it stood just before your words: the agent's thread in its
home channel, where the voice focus takes you to it, cut there and put in
the throwaway database. Single turns with no history can't show how a model
does deep in a conversation; these can. The production file is only read,
before the stores are pointed away from it. A live case expects only that
its agent answers, and the judge (scripts/voice_judge.py) sees the last of
the conversation before your words.

Without Discord there is no playback, so the reported time to first audio is
`tts_first_byte`: the moment the first chunk's audio is ready to play.

Model x thinking benchmark, to pick the voice graph's light model:
--combo TIER:LEVEL runs every case on each combo given, with every agent
model call of the run -- main's and the room's agent's -- on that tier and
thinking level (`combo_scope`). `prod` is production as configured, and
--matrix is the full set: FLASH_LITE, FLASH and PRO at minimal, low and
medium (PRO has no minimal), plus prod. Each result records its combo, and
scripts/voice_judge.py compares every combo's replies with the reference's:
PRO:low by default, PRO at the voice default level, since the light model's
bar is set against PRO. Each run of a case goes on every combo back to back,
so all of them see the same live data.
--light TIER:LEVEL runs every combo with the voice graph's light first call
on that model (graphs/voice: the turn's first line on it, then the agent's
own model), `off` without it; each goes in the label
(`prod+light:FLASH_LITE:minimal`), so the judge holds the light call's runs
to PRO:low without it. Without --light, VOICE_LIGHT_MODEL decides: FLASH_LITE
at minimal unless it's set, so `--light off` is how to run without it. The
light call is held to its own model and level, and isn't a round trip of the
case's budget. It only says the first line, so a run where the agent's own
model didn't follow it fails (`light_misses`).
It's safe for the rate limit the live bot shares: one run at a time, --rpm
caps the model calls a minute (counted from each run's trace), a run the
API refuses with 429 / RESOURCE_EXHAUSTED waits and runs again, and --out
is read first and rewritten after every run, so rerunning the same command
resumes where it stopped.
Before it times any combo, it checks the service: two one-word calls on each
tier the combos run on. When every call of a tier comes slower than
SLOW_FIRST_CHUNK_S, or any one hangs or fails, it runs nothing and exits 4,
since the latency would measure the service's bad day instead of the combos;
--anyway runs regardless.

Usage:
    python scripts/voice_dryrun.py                        # every read-only case, once
    python scripts/voice_dryrun.py --case lights-status --runs 3
    python scripts/voice_dryrun.py --stt live             # speak it into the VAD and streaming STT first
    python scripts/voice_dryrun.py --out results.json
    python scripts/voice_dryrun.py --combo FLASH_LITE:minimal,FLASH:low --case lights-status
    python scripts/voice_dryrun.py --matrix --runs 3 --rpm 20 --out sessions/voice_bench/results.json
    python scripts/voice_dryrun.py --combo prod --light FLASH_LITE:minimal,FLASH:minimal --runs 3 --out results.json
    python scripts/voice_dryrun.py --live Ted,Aki --light off,FLASH_LITE:minimal --runs 2 --out live.json
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Dict, Iterator, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Constants only: importing them loads none of the runtime, which waits for isolate_sessions.
from core.config.models import (  # noqa: E402
    THINKING_BY_SURFACE, THINKING_LEVELS, TIER_ORDER, TIER_THINKING_LEVELS, tier_and_level)

CASES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_replay_cases.json")
REPLAY_SOURCE = "voice_replay"
# `!python` runs this without `--yes` (core/runtime/commands/typed/python.py):
# a replay is read-only, except with --allow-mutations, which needs the confirmation.
READ_ONLY = True
WRITES_WITH = ("--allow-mutations",)

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

    Taken from each tool's own read bundle, or from the `READ_ACTIONS` every
    tool that can write declares, in tools/ and in each agent's own tools/, so a
    new read action or a new write tool needs no change here. Tools that declare
    neither (yfinance, seats_aero, zillow_query, ...) have no write actions to guard.
    """
    import importlib
    import importlib.util

    from core.loaders.tools_loader import ToolsLoader

    loader = ToolsLoader()

    def module(tool):
        return importlib.import_module(loader.module_path(tool))

    allowed = {
        "filesystem": set(module("filesystem").PERMISSION_BUNDLES["@read"]),
        "home_assistant": set(module("home_assistant").PERMISSION_BUNDLES["@observe"]),
        "bash": set(),
    }
    for tool in sorted((set(loader._discover_tools()) | set(loader._agent_tools())) - set(allowed)):
        # The module's file, without importing it: agent folders keep their hyphens.
        origin = importlib.util.find_spec(loader.module_path(tool)).origin
        with open(origin, encoding="utf-8") as f:
            declares = re.search(r"^READ_ACTIONS\b", f.read(), re.MULTILINE)
        if declares:  # import only the tools that declare it
            allowed[tool] = set(module(tool).READ_ACTIONS)
    return allowed


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
        if kwargs.get("probe"):  # a question about what's allowed, not an action
            return original(self, ctx, tool_id, action_name, path, **kwargs)
        if allowed is not None and action_name is not None and action_name not in allowed:
            BLOCKED_WRITES.append((tool_id, action_name, path))
            print(f"[replay] refused {tool_id}.{action_name} on {path} (read-only replay)", file=sys.stderr)
            return False
        return original(self, ctx, tool_id, action_name, path, **kwargs)

    ToolsLoader.check_permission = check_permission
    tools.gog.subprocess = _ReadOnlySubprocess(real_subprocess)


# --- combos: the model x thinking benchmark ----------------------------------

PROD = "prod"  # production as configured: each agent's own model and thinking
# What scripts/voice_judge.py compares every combo with: PRO at the voice
# default level, since the light model's bar is set against PRO's replies.
REFERENCE = f"PRO:{THINKING_BY_SURFACE['voice']}"
MATRIX_LEVELS = ("minimal", "low", "medium")  # voice caps the model's own picks at medium


@dataclass(frozen=True)
class Combo:
    """The tier and thinking level every agent model call of a run is held to;
    None keeps production's choice for that half. `light` is the voice graph's
    light first call, `TIER:level` (graphs/voice), or None for none."""
    model: Optional[str] = None
    thinking: Optional[str] = None
    light: Optional[str] = None

    @property
    def label(self) -> str:
        if self.model is None and self.thinking is None:
            base = PROD
        else:
            base = f"{self.model or PROD}:{self.thinking or PROD}"
        return f"{base}+light:{self.light}" if self.light else base


def parse_combo(text: str) -> Combo:
    """`FLASH:low`; `FLASH` keeps production's thinking, `prod:low` each agent's
    own model, `prod` both. Checked against core/config/models.py, down to the
    levels each tier takes: a PRO:minimal run would quietly be PRO:low."""
    tier, _, level = text.strip().partition(":")
    tier, level = tier.strip().upper().replace("-", "_"), level.strip().lower()
    model = None if tier in ("", PROD.upper()) else tier
    thinking = None if level in ("", PROD) else level
    if model is not None and model not in TIER_ORDER:
        raise ValueError(f"unknown tier {tier!r} in {text!r}: use {', '.join(TIER_ORDER)} or {PROD}")
    if thinking is not None and thinking not in THINKING_LEVELS:
        raise ValueError(f"unknown thinking level {level!r} in {text!r}: use {', '.join(THINKING_LEVELS)} or {PROD}")
    if model and thinking and thinking not in TIER_THINKING_LEVELS[model]:
        raise ValueError(f"{model} doesn't take {thinking} thinking: use {', '.join(TIER_THINKING_LEVELS[model])}")
    return Combo(model, thinking)


def parse_light(text: Optional[str]) -> Optional[str]:
    """The voice graph's light model as `TIER:level` (`FLASH_LITE:minimal`),
    checked as VOICE_LIGHT_MODEL is; None for `off` or nothing."""
    text = (text or "").strip()
    if not text or text.lower() == "off":
        return None
    return ":".join(tier_and_level(text))


def matrix() -> List[Combo]:
    """The full matrix: each agent tier at each of MATRIX_LEVELS it takes, then prod."""
    return [Combo(tier, level) for tier in TIER_ORDER for level in MATRIX_LEVELS
            if level in TIER_THINKING_LEVELS[tier]] + [Combo()]


def combos_from(model: Optional[str] = None, thinking: Optional[str] = None,
                combos: Optional[List[str]] = None, use_matrix: bool = False,
                lights: Optional[List[Optional[str]]] = None) -> List[Combo]:
    """The combos asked for, in order and each once: --matrix's, every --combo
    (repeated or comma-separated), then --model with --thinking as one more.
    None asked for: prod. Each goes with every light model in `lights`
    (`TIER:level`, or None for none); without any, with none."""
    found = matrix() if use_matrix else []
    found += [parse_combo(text) for value in combos or [] for text in value.split(",") if text.strip()]
    if model or thinking:
        found.append(parse_combo(f"{model or PROD}:{thinking or PROD}"))
    bases = list(dict.fromkeys(found)) or [Combo()]
    return list(dict.fromkeys(replace(base, light=light) for base in bases for light in (lights or [None])))


# Agents built under each model a combo forces: a compiled graph has its model
# built in (Agent._get_graph caches it), so each forced model gets agents of
# its own, kept for the next runs on it.
_AGENTS_BY_MODEL: Dict[str, dict] = {}


@contextmanager
def combo_scope(combo: Combo) -> Iterator[None]:
    """Holds every agent model call made inside the block to `combo`.

    A voice turn starts on main and is handed to the room's agent, which runs
    on a context of its own (core/runtime/delegation.py), so an override on
    the turn's context would never reach it, and `with_agent` drops one too.
    So, in this process only, this replaces what every agent's calls pass
    through instead: the model GraphBuilder builds a graph with
    (`chat_model`), and the level the model wrapper sends each call at
    (`resolve_thinking_level`; the model's own <next_thinking> pick is still
    taken, but not applied). Stateless workers keep their own model and level,
    as they do on the voice graph. `combo_misses` then checks the trace for a
    call this missed.
    """
    if combo.model is None and combo.thinking is None:
        yield
        return
    import core.runtime.graph_builder as graph_builder
    import core.runtime.model.retry as retry
    from core.config.models import clamp_thinking_level
    from core.loaders.agents_loader import AgentsLoader
    from core.runtime.foundation.context import try_context

    loader = AgentsLoader()
    build, resolve, agents = graph_builder.chat_model, retry.resolve_thinking_level, loader._agents_cache

    def chat_model(ctx, agent_config):
        if agent_config.get("stateless"):
            return build(ctx, agent_config)
        return build(replace(ctx, model=combo.model), agent_config)

    def resolve_thinking_level(model_id):
        level = resolve(model_id)  # takes the model's pick, which this level overrides
        ctx = try_context()
        if ctx is None or (loader.get_agent_config(ctx.agent_id) or {}).get("stateless"):
            return level
        return clamp_thinking_level(combo.thinking, model_id)

    if combo.model:
        graph_builder.chat_model = chat_model
        loader._agents_cache = _AGENTS_BY_MODEL.setdefault(combo.model, {})
    if combo.thinking:
        retry.resolve_thinking_level = resolve_thinking_level
    try:
        yield
    finally:
        graph_builder.chat_model, retry.resolve_thinking_level, loader._agents_cache = build, resolve, agents


@contextmanager
def light_scope(light: Optional[str]) -> Iterator[None]:
    """Runs the block with the voice graph's light first call on `light`
    (`TIER:level`), or off for None, whatever VOICE_LIGHT_MODEL says
    (core/runtime/graph_builder.py)."""
    from core.config.settings import Config

    with Config().overriding(voice_light_model=light or "off"):
        yield


def combo_misses(combo: Combo, model_calls: List[dict]) -> List[str]:
    """The run's agent model calls that weren't on `combo`, as failures: such a
    run would be mislabeled, so a change to where models are chosen fails
    loudly here instead of skewing the benchmark. A light first call (`light`
    on its trace event) is held to the combo's light model instead."""
    from core.config.models import clamp_thinking_level, resolve_model

    light = tier_and_level(combo.light) if combo.light else None

    def miss(call: dict) -> Optional[str]:
        model = str(call.get("model") or "").removeprefix("models/")
        if call.get("light"):
            if light is None:
                return f"{call.get('agent')} made a light call"
            if (model, call.get("thinking")) != (resolve_model(light[0]), light[1]):
                return f"{call.get('agent')}'s light call ran {model} at {call.get('thinking')}"
        elif combo.model and model != resolve_model(combo.model):
            return f"{call.get('agent')} ran {model}"
        elif combo.thinking and call.get("thinking") != clamp_thinking_level(combo.thinking, model):
            return f"{call.get('agent')} thought at {call.get('thinking')}"
        return None

    misses = [m for m in map(miss, model_calls) if m]
    if misses:  # a stateless worker keeps its own model and level: never a miss
        from core.loaders.agents_loader import AgentsLoader

        loader = AgentsLoader()
        misses = [m for call in model_calls if (m := miss(call))
                  and not (loader.get_agent_config(call.get("agent")) or {}).get("stateless")]
    return [f"not on {combo.label}: {m}" for m in dict.fromkeys(misses)]


def light_misses(model_calls: List[dict]) -> List[str]:
    """Light calls that no call on the agent's own model followed, as failures.
    The light model only says a turn's first line, so the answer is the
    agent's model's: without it the turn ended on that line. A light call
    that fell back is followed by the same call on the agent's model."""
    misses = [f"{call.get('agent')}'s own model never ran after its light call"
              for at, call in enumerate(model_calls)
              if call.get("light") and not any(not later.get("light") and later.get("agent") == call.get("agent")
                                               for later in model_calls[at + 1:])]
    return list(dict.fromkeys(misses))


# The light call's events on a run's trace (core/runtime/model/handover.py,
# graphs/voice): it handed over, fell back to the agent's model, or broke off.
LIGHT_EVENTS = ("handover", "light_fallback", "light_cut")


@dataclass
class CaseResult:
    case_id: str
    run: int
    marks: Dict[str, int]
    combo: str = PROD           # the Combo label the run's agent model calls were held to
    room: str = ""
    reply: str = ""
    transcript: str = ""
    context: str = ""           # a live case's conversation before the words, for the judge
    spoken: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    agents: List[str] = field(default_factory=list)
    model_calls: List[dict] = field(default_factory=list)
    model_replies: List[dict] = field(default_factory=list)   # each call's first streamed chunk and end
    thinking_picks: List[dict] = field(default_factory=list)  # the model's <next_thinking> picks: level, applied
    light_events: List[dict] = field(default_factory=list)    # LIGHT_EVENTS, as traced
    tokens: Dict[str, int] = field(default_factory=dict)
    blocked_writes: List[str] = field(default_factory=list)
    speech: str = ""            # the <speech> text the router heard
    shown: str = ""             # what the text channel would show
    speech_missing: bool = False
    errored: bool = False       # the run failed: it speaks the error line and isn't held to <speech>
    failures: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures

    @classmethod
    def from_dict(cls, data: dict) -> "CaseResult":
        """A run as --out stored it; `passed` is worked out again."""
        names = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in names})


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
    # The agent's own round trips: the light first call, said while they run, isn't one.
    calls = [call for call in result.model_calls if not call.get("light")]
    if "max_model_calls" in expect and len(calls) > expect["max_model_calls"]:
        failures.append(f"{len(calls)} model calls, budget {expect['max_model_calls']}")
    return failures


# --- the speech contract ----------------------------------------------------

_UNIT_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS_WORDS = {w: 10 * i for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2)}
_UNIT_ORDINALS = {w: i for i, w in enumerate(
    "zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth "
    "thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth".split())}
_TENS_ORDINALS = {w: 10 * i for i, w in enumerate(
    "twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth".split(), start=2)}
_MONTHS = {w: i for i, w in enumerate(
    "january february march april may june july august september october november december".split(), start=1)}
# "an hour", "a dollar": the article is the number.
_ONE_UNITS = {"hour", "minute", "day", "week", "month", "year", "dollar", "mile", "pound", "cup", "night"}
# Speech that hands the rest to the channel (long lists, links): misses are noted, not failed.
DEFERS_RE = re.compile(r"\b(?:in|on) the (?:text )?channel\b|\b(?:want|shall|should) I (?:continue|go on|keep going)\b",
                       re.IGNORECASE)
# Capitalized words that aren't names.
_NOT_NAMES = {"i", "i'm", "i'll", "i've", "i'd", "ok", "okay", "am", "pm", "tl", "dr"}
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
# A label: "**Due Date**:", "**Due Date:**", or a line opening "Due Date:" (the colon may be full-width).
_LABEL_RE = re.compile(r"\*\*([^*\n]+?)\*\*\s*[:：]|\*\*([^*\n]+?)[:：]\s*\*\*"
                       r"|^([A-Z][\w'’]*(?:[ &/]+[A-Z][\w'’]*){0,4})\s*[:：](?=\s)")
# What a capitalized word follows when it opens a sentence or a phrase.
_PHRASE_STARTS = ".!?:;：(-–—|/"


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


# A time of day in digits, a number with thousands separators, and a plain one.
_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_PLAIN = re.compile(r"\d+(?:\.\d+)?")


def _plain_number(token: str) -> Optional[str]:
    """`token` as one plain number, its thousands separators out ("1,409.38"
    gives "1409.38"); None when it isn't one number: a version (1.2.3), an
    address, digits run together with commas."""
    if _THOUSANDS.fullmatch(token):
        token = token.replace(",", "")
    return token if _PLAIN.fullmatch(token) else None


def spoken_numbers(text: str) -> Set[str]:
    """Every number `text` says, as digits: "45", "forty-five" and "forty five"
    all give "45"; "seven thirty" gives "7" and "30"; "October" gives "10"."""
    found: Set[str] = set()
    words = re.findall(r"\d+(?:[.,:]\d+)*|[a-z]+|[,.;:!?]", text.lower().replace("-", " ").replace("’", "'"))
    total, group = 0, None

    def close():
        nonlocal total, group
        if group is not None or total:
            found.add(_number(total + (group or 0)))
        total, group = 0, None

    i = 0
    while i < len(words):
        word = words[i]
        unit = _UNIT_WORDS.get(word, _UNIT_ORDINALS.get(word))
        tens = _TENS_WORDS.get(word, _TENS_ORDINALS.get(word))
        if word[0].isdigit():
            close()
            found.update(_digit_forms(word))
        elif unit is not None:
            joins = (group is None and total > 0) or (group is not None and (
                (unit < 10 and group % 100 >= 20 and group % 10 == 0) or (group % 100 == 0 and group > 0)))
            if not joins:
                close()
            group = (group or 0) + unit if joins else unit
        elif tens is not None:
            joins = (group is None and total > 0) or (group is not None and group % 100 == 0 and group > 0)
            if not joins:
                close()
            group = (group or 0) + tens if joins else tens
        elif word == "hundred" and group is not None:
            group *= 100
        elif word == "thousand" and (group is not None or total):
            total, group = total + (group or 1) * 1000, None
        elif word == "and" and words[i + 1:i + 3] == ["a", "half"] and group is not None:
            found.add(_number(total + group + 0.5))
            total, group = 0, None
            i += 2
        elif word == "point" and group is not None and i + 1 < len(words) and _UNIT_WORDS.get(words[i + 1], 10) < 10:
            digits = ""
            while i + 1 < len(words) and _UNIT_WORDS.get(words[i + 1], 10) < 10:
                i += 1
                digits += str(_UNIT_WORDS[words[i]])
            found.add(f"{total + group}.{digits}")
            total, group = 0, None
        elif word in ("a", "an") and i + 1 < len(words) and words[i + 1].rstrip("s") in _ONE_UNITS:
            close()
            found.add("1.5" if words[i + 2:i + 5] == ["and", "a", "half"] else "1")
        elif word == "free":
            close()
            found.add("0")
        elif word in _MONTHS:
            close()
            found.add(str(_MONTHS[word]))
        elif word == "and" and group is not None and group >= 100:
            pass  # "one hundred and five"
        else:
            close()
        i += 1
    close()
    return found


def _digit_forms(token: str) -> Set[str]:
    """A number written in digits, in the forms speech may give it."""
    if ":" in token:  # a time: the hour either way round the clock, and the minutes
        time = _TIME.fullmatch(token)
        if not time:
            return set()
        hour, minutes = int(time.group(1)), int(time.group(2))
        return {str(hour), str(hour % 12 or 12)} | ({str(minutes)} if minutes else set())
    token = token.replace(",", "")
    try:
        return {_number(float(token))}
    except ValueError:
        return set()


def _required_numbers(reply: str) -> List[Tuple[str, List[Set[str]]]]:
    """(label, alternatives): each number of the reply that speech must keep, with
    the sets of spoken forms that count as keeping it. What isn't one number --
    a version (1.2.3), an address, a ratio (16:9) -- isn't required."""
    text = re.sub(r"```.*?```|`[^`]*`|https?://\S+|\]\([^)]*\)|\S*/\S*", " ", reply, flags=re.DOTALL)
    text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", text, flags=re.MULTILINE)  # list markers
    required = []
    for match in re.finditer(r"(\$)?(\d+(?:[.,:]\d+)*)", text):
        money, token = match.group(1), match.group(2)
        iso = re.fullmatch(r"(\d{4})", token) and re.match(r"-(\d{2})-(\d{2})", text[match.end():])
        if iso:  # 2026-10-16: the month and the day
            required.append((f"{token}{iso.group(0)}", [{str(int(iso.group(1))), str(int(iso.group(2)))}]))
            continue
        if re.fullmatch(r"\d{5,}", token.replace(",", "")) or re.fullmatch(r"(?:19|20)\d\d", token):
            continue  # an ID, or a year
        if re.match(r"-\d", text[match.end():match.end() + 2]) or re.search(r"\d-$", text[:match.start()]):
            continue  # the rest of an ISO date
        if ":" in token:
            time = _TIME.fullmatch(token)
            if not time:
                continue  # not a time of day
            hour, minutes = int(time.group(1)), int(time.group(2))
            alternatives = [{str(hour)}, {str(hour % 12 or 12)}]
            if minutes:
                alternatives = [a | {str(minutes)} for a in alternatives]
            required.append((token, alternatives))
            continue
        number = _plain_number(token)
        if number is None:
            continue  # not one number
        label = (money or "") + token
        if "." in number:
            whole, _, fraction = number.partition(".")
            if money:  # $3.50: dollars and cents
                forms = {str(int(whole))} | ({str(int(fraction))} if int(fraction) else set())
            else:
                forms = {str(int(whole)), str(int(fraction))}
            required.append((label, [{_number(float(number))}, forms]))
        else:
            required.append((label, [{_number(float(number))}]))
    return required


def _words(text: str) -> List[str]:
    """`text` as lowercase words, possessives dropped."""
    return re.findall(r"[a-z0-9]+", text.lower().replace("’", "'").replace("'s", ""))


def _required_names(reply: str) -> List[str]:
    """Named items of the reply: bold phrases, runs of capitalized words
    ("Point Reyes", "Sample Diner"), and single capitalized words in list items
    ("Pat", "Teahouse").

    Not counted: headings; field labels, which are short or repeat on every
    item ("**Due Date**:") -- a longer one-off label is the item's own name;
    and a single capitalized word that opens a sentence or phrase, or sits in
    running prose, as often a greeting ("Good morning Sam") or a stressed
    word as a name.
    """
    text = re.sub(r"```.*?```|`[^`]*`|https?://\S+|\]\([^)]*\)", " ", reply, flags=re.DOTALL)
    lines = [_LIST_MARKER_RE.sub("- ", line) for line in text.splitlines()
             if not re.match(r"\s*#|\s*\*\*[^*]+\*\*\s*[:：]?\s*$", line)]  # headings, bold title lines
    labels = [next(g for g in m.groups() if g).strip() for line in lines
              for m in _LABEL_RE.finditer(line[2:] if line.startswith("- ") else line)]
    counts = Counter(label.lower() for label in labels)
    names = [label for label in labels if len(_words(label)) > 2 and counts[label.lower()] == 1]
    for line in lines:
        listed = line.startswith("- ")
        line = _LABEL_RE.sub(":", line[2:] if listed else line)
        names += [b for b in re.findall(r"\*\*(.+?)\*\*", line) if not re.match(r"\W*\d", b)]  # not "**$25**"
        plain = re.sub(r"[*_>]", "", re.sub(r"\*\*.+?\*\*", "·", line))  # a bold name counts once, whole
        for match in re.finditer(r"(?<![\w'’])[A-Z][\w'’]*(?:\s+[A-Z][\w'’]*)*", plain):
            words = [w for w in match.group(0).split()
                     if w.lower().rstrip("'’s") not in _NOT_NAMES and not (w.isupper() and len(w) <= 3)]
            before = plain[:match.start()].rstrip()
            opens = not before or before[-1] in _PHRASE_STARTS
            if len(words) > 1 or (words and listed and not opens):
                names.append(" ".join(words))
    seen, unique = set(), []
    for name in names:
        name = " ".join(re.sub(r"[^\w\s'’&-]", "", name).split())
        if name and any(c.isalpha() for c in name) and name.lower() not in seen:
            seen.add(name.lower())
            unique.append(name)
    return unique


def _said(word: str, spoken: Set[str]) -> bool:
    """Whether `word`, or a form of it ("planning" for "planner"), was spoken."""
    return any(s.startswith(word) or (len(word) >= 5 and s[:5] == word[:5]) for s in spoken)


def missed_numbers(reply: str, speech: str) -> List[str]:
    """The numbers of `reply` that `speech` doesn't say, in digits or words."""
    said = spoken_numbers(speech)
    missed = [label for label, alternatives in _required_numbers(reply)
              if not any(forms <= said for forms in alternatives)]
    return list(dict.fromkeys(missed))


def missed_names(reply: str, speech: str) -> List[str]:
    """The named items of `reply` that `speech` may not say: one counts as said
    when each of its words of three or more letters is, in some form."""
    spoken = set(_words(speech))
    missed = []
    for name in _required_names(reply):
        parts = [w for w in _words(name) if len(w) >= 3]
        if parts and not all(_said(w, spoken) for w in parts):
            missed.append(name)
    return missed


def speech_coverage(reply: str, speech: str) -> List[str]:
    """The numbers and named items of `reply` that `speech` doesn't say.

    Links, code, paths, IDs, years and list numbering are not required -- the
    speech is told to leave those to the channel.
    """
    return missed_numbers(reply, speech) + missed_names(reply, speech)


def foreign_words(speech: str) -> List[str]:
    """Words of `speech` in a script other than Latin, which an English voice can't say."""
    def foreign(c):
        return unicodedata.category(c).startswith("L") and not (ord(c) < 0x250 or 0x1E00 <= ord(c) <= 0x1EFF)
    words = [w.strip(",.;:!?()、，。") for w in speech.split() if any(foreign(c) for c in w)]
    return list(dict.fromkeys(words))


# The local-time stamp user messages carry ("[Fri 06:42]"); replies must not copy it.
STAMP_RE = re.compile(r"\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{2}:\d{2}\]")


def check_speech(result: "CaseResult") -> Tuple[List[str], List[str]]:
    """The speech contract every run is held to: (failures, notes)."""
    failures, notes = [], []
    # A failed run's reply is whatever had streamed, cut off anywhere; the channel shows `shown`.
    text = result.shown if result.errored else result.reply + result.shown
    if re.search(r"</?speech\b", text, re.IGNORECASE):
        failures.append("<speech> reached the text")
    if STAMP_RE.search(text) or STAMP_RE.search(result.speech):
        failures.append("the reply copies a message time stamp")
    if result.errored:
        return failures, notes  # the error is the failure; production doesn't count it either
    if result.speech_missing:
        failures.append("no <speech> block (the fallback was spoken)")
        return failures, notes
    foreign = foreign_words(result.speech)
    if foreign:
        failures.append(f"speech isn't in English: {', '.join(foreign)}")
    defers = " (it defers to the channel)" if DEFERS_RE.search(result.speech) else ""
    numbers = missed_numbers(result.reply, result.speech)
    if numbers:
        (notes if defers else failures).append(f"speech missed: {', '.join(numbers)}{defers}")
    names = missed_names(result.reply, result.speech)
    if names:
        notes.append(f"speech may have missed: {', '.join(names)}{defers}")
    return failures, notes


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
        notes = r.failures + r.notes + [f"refused write: {w}" for w in r.blocked_writes] + _picks(r) + _light_notes(r)
        lines.append(
            f"| {r.case_id} | {r.run} | {'PASS' if r.passed else 'FAIL'} "
            f"| {_ms(r.marks.get('first_token'))} | {_ms(r.marks.get('first_chunk'))} | {_ms(r.marks.get('tts_first_byte'))} "
            f"| {len(r.model_calls)} ({calls}) | {r.tokens.get('input', 0)}/{r.tokens.get('output', 0)} "
            f"| {'; '.join(notes)} |"
        )
    lines += ["", "| Stage | p50 | p90 | n |", "|---|---|---|---|"]
    for stage, p50, p90, n in stage_table(results):
        lines.append(f"| {stage} | {_ms(p50)} | {_ms(p90)} | {n} |")
    passed = sum(r.passed for r in results)
    held = [r for r in results if not r.errored]
    with_speech = sum(not r.speech_missing for r in held)
    speech_line = f"Speech: {with_speech}/{len(held)} runs came with a <speech> block."
    if len(held) < len(results):
        speech_line += f" ({len(results) - len(held)} failed runs aren't counted.)"
    lines += ["", f"Quality: {passed}/{len(results)} runs passed.", speech_line]
    light = _light_line(results)
    if light:
        lines.append(light)
    return "\n".join(lines)


def render_combos(results: List[CaseResult], combos: Optional[List[str]] = None) -> str:
    """`render_report` for each combo in turn (in `combos`' order), under its
    label; a replay on a single combo reads as it always did."""
    labels = combos or list(dict.fromkeys(r.combo for r in results))
    labels = [label for label in labels if any(r.combo == label for r in results)]
    if len(labels) <= 1:
        return render_report(results)
    return "\n\n".join(f"### {label}\n\n" + render_report([r for r in results if r.combo == label])
                       for label in labels)


def _picks(r: CaseResult) -> List[str]:
    """The model's <next_thinking> picks: the level applied, and what it asked for when capped.
    A combo that sets the level overrides them, so there they're only what it asked for.
    A light first call doesn't set it: `prod+light:...` keeps production's levels."""
    base = r.combo.split("+light:")[0]
    if base != PROD and not base.endswith(f":{PROD}"):
        return [f"asked {p['level']} (the combo's level held)" for p in r.thinking_picks]
    return [f"picked {p['applied']}" + (f" (asked {p['level']})" if p["level"] != p["applied"] else "")
            for p in r.thinking_picks]


def _light_notes(r: CaseResult) -> List[str]:
    """The run's light call, when it didn't end as it should, handing over."""
    return [f"light call fell back: {e.get('reason')}" if e.get("kind") == "light_fallback" else "light call broke off"
            for e in r.light_events if e.get("kind") in ("light_fallback", "light_cut")]


def _light_line(results: List[CaseResult]) -> Optional[str]:
    """How the runs' light calls ended; None when no run made one."""
    events = [e for r in results for e in r.light_events]
    if not events:
        return None
    kinds = Counter(e.get("kind") for e in events)
    reasons = Counter(str(e.get("reason")) for e in events if e.get("kind") == "light_fallback")
    why = f" ({', '.join(f'{reason}: {n}' for reason, n in reasons.most_common())})" if reasons else ""
    return (f"Light calls: {kinds['handover']} handed over, {kinds['light_fallback']} fell back{why}, "
            f"{kinds['light_cut']} broke off.")


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
    from core.runtime.foundation import jobs

    os.makedirs(directory, exist_ok=True)
    db_path = os.path.join(directory, "memory.db")
    # Config().memory_db_path: the core/storage stores (sessions and jobs among them), the
    # checkpointer and the voice data follow it.
    os.environ["MEMORY_DB_PATH"] = db_path
    jobs.JobManager._instance = None
    return db_path


def reset_history(db_path: str) -> None:
    """Deletes every checkpoint in the isolated DB, so the next case starts with no history."""
    from core.storage.sessions import CHECKPOINT_ENTRIES, SessionStore

    store = SessionStore(db_path)
    if not store.exists():
        return
    with store.connect() as conn:
        store.delete_entries(store.tables(include_archived=True, conn=conn), CHECKPOINT_ENTRIES, conn=conn)


def replay_tokens(since: float, db_path: str) -> Dict[str, int]:
    """Input/output tokens every session wrote since `since` (epoch seconds) in the isolated DB."""
    from core.storage.sessions import SessionStore

    totals = {"input": 0, "output": 0}
    store = SessionStore(db_path)
    if not store.exists():
        return totals
    totals["input"], totals["output"] = store.token_sums(since)
    return totals


@contextmanager
def without_pruning() -> Iterator[None]:
    """Runs the block with no history prune (core/runtime/hooks.py PruneHook).
    A turn starts one after it, in the background, and the next case's turn
    comes at once: the prune could save its rewrite of the last case's
    history over the history the next case was just given."""
    from core.runtime.hooks import PruneHook, set_turn_hooks, turn_hooks

    hooks = turn_hooks()
    set_turn_hooks([hook for hook in hooks if not isinstance(hook, PruneHook)])
    try:
        yield
    finally:
        set_turn_hooks(hooks)


# --- live cases: your own conversations, from production's memory.db ----------

# Turns per conversation that --live makes cases of: the latest ones.
LIVE_TURNS = 8
# What the judge sees of the conversation before a live case's words: the last
# LIVE_CONTEXT_MESSAGES of your messages and the agent's replies, each cut to
# LIVE_CONTEXT_CHARS.
LIVE_CONTEXT_MESSAGES = 8
LIVE_CONTEXT_CHARS = 500
# Who passed your words on to a delegated agent (core/runtime/delegation.py
# `with_caller`): it's in the agent's stored messages, not in what you said.
CALLER_RE = re.compile(r"<caller>.*?</caller>", re.DOTALL)


def _text(content) -> str:
    """A stored message's text: its content, or the text parts of a list of them."""
    if isinstance(content, str):
        return content
    return "".join(part if isinstance(part, str) else part.get("text") or "" for part in content or []
                   if isinstance(part, str) or (isinstance(part, dict) and part.get("type") == "text"))


def user_words(content) -> str:
    """Your words in a delegated agent's stored message or transcript row: on
    one line, without the time stamps and the <caller> tag the runtime adds."""
    from core.prompts.system import TIME_STAMP

    return " ".join(TIME_STAMP.sub(" ", CALLER_RE.sub(" ", _text(content))).split())


def agent_words(content) -> str:
    """What a stored reply said aloud and posted, on one line: its <speech>,
    then its text. The parser takes out what no one heard or saw (the memory
    log; the <next_thinking> marker never reaches the stored reply)."""
    from core.channel.response_parser import AgentResponse

    response = AgentResponse.from_string(_text(content))
    return " ".join(" ".join(part for part in (response.speech, response.text) if part).split())


def _cut(text: str, limit: int = LIVE_CONTEXT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def render_context(messages: list, agent_name: str, limit: int = LIVE_CONTEXT_MESSAGES) -> str:
    """The conversation in `messages`, for the judge: the last `limit` of your
    messages and the agent's replies with words, oldest first, one a line.
    Tool calls and results, and the runtime's summaries, are left out."""
    from langchain_core.messages import AIMessage, HumanMessage

    lines = []
    for message in messages:
        if isinstance(message, HumanMessage):
            who, words = "User", user_words(message.content)
        elif isinstance(message, AIMessage):
            who, words = agent_name, agent_words(message.content)
        else:
            continue
        if words:
            lines.append(f"{who}: {_cut(words)}")
    return "\n".join(lines[-limit:])


def answered(messages: list, at: int) -> bool:
    """Whether a reply with words came after the HumanMessage at `at`, before the next one."""
    from langchain_core.messages import AIMessage, HumanMessage

    for message in messages[at + 1:]:
        if isinstance(message, HumanMessage):
            return False
        if isinstance(message, AIMessage) and agent_words(message.content):
            return True
    return False


def voice_words(conn, table: str) -> Set[str]:
    """Your words (`user_words`) of each turn in session table `table` that
    came by voice: its transcript row from `user`, then the turn's first token
    row, on the voice surface. Every model call writes a token row, and the
    turn's first also writes your words before it
    (core/runtime/observability/transcript.py)."""
    from core.storage.util import ident

    words, pending = set(), None
    for row in conn.execute(f"SELECT entry_type, from_role, message, surface FROM {ident(table)} "
                            f"WHERE entry_type IN ('message', 'token') ORDER BY id"):
        if row["entry_type"] == "message":
            if row["from_role"] == "user":
                pending = row["message"]
        elif pending is not None:
            if row["surface"] == "voice":
                words.add(user_words(pending))
            pending = None
    return words


def read_conversation(db_path: str, thread_id: str) -> Tuple[Optional[dict], Set[str]]:
    """The newest checkpoint of `thread_id` in the memory.db at `db_path`, as
    its saver stored it ({"checkpoint", "metadata", "new_versions", ...}), or
    None; and the words of its voice turns (`voice_words`). Read over a
    connection that can't write: production's file is never opened for writing."""
    from core.knowledge.memory.sqlite_checkpointer import read_blob
    from core.storage.sessions import SessionStore, sanitize_table_name
    from core.storage.util import ident, table_exists

    table = sanitize_table_name(thread_id)
    with SessionStore(db_path).read_only() as conn:
        if not table_exists(conn, table):
            return None, set()
        row = conn.execute(f"SELECT data FROM {ident(table)} WHERE entry_type = 'checkpoint' "
                           f"ORDER BY step DESC, id DESC LIMIT 1").fetchone()
        return (read_blob(row["data"]) if row else None), voice_words(conn, table)


def live_cases(names: List[str], turns: int = LIVE_TURNS, db_path: Optional[str] = None,
               only: Optional[List[str]] = None) -> List[dict]:
    """Cases from your conversations with these agents (ids or names): the
    last `turns` turns of each that you spoke and the agent answered, from the
    memory.db at `db_path` -- production's by default, so this comes before
    `isolate_sessions`.

    An agent's conversation is its thread in its home channel, where the
    voice focus takes you to it (core/runtime/foundation/routing.py
    `home_channel`): `<agent>:tool:<channel>`, the session a turn routed to it
    runs on. Its newest checkpoint holds the history the agent saw. A case
    keeps that history cut just before its words, for `seed_history`, and the
    last of it as text for the judge. It holds message objects, so cases are
    never written out; their results are.
    """
    from langchain_core.messages import HumanMessage

    from core.channel.handoff import agent_named
    from core.config.settings import Config
    from core.loaders.agents_loader import AgentsLoader
    from core.runtime.foundation.routing import home_channel

    db_path = db_path or Config().memory_db_path
    if not os.path.isfile(db_path):
        raise ValueError(f"there's no memory.db at {db_path} to take conversations from")
    loader = AgentsLoader()
    cases: Dict[str, dict] = {}
    for name in (name.strip() for name in names):
        agent = agent_named(name, loader)
        if agent is None:
            raise ValueError(f"no agent has the id or name {name!r}")
        room = home_channel(agent, loader)
        if room is None:
            raise ValueError(f"{agent} answers no channel of its own, so it has no conversation to take")
        thread = f"{agent}:tool:{room}"
        stored, voiced = read_conversation(db_path, thread)
        checkpoint = (stored or {}).get("checkpoint") or {}
        messages = list((checkpoint.get("channel_values") or {}).get("messages") or [])
        agent_name = (loader.get_agent_config(agent) or {}).get("name") or agent
        picked = []
        for at in reversed(range(len(messages))):
            if len(picked) == turns:
                break
            message = messages[at]
            if not (isinstance(message, HumanMessage) and user_words(message.content) in voiced
                    and answered(messages, at)):
                continue
            context = render_context(messages[:at], agent_name)
            if context:  # a turn with no conversation before it is what the case file has
                picked.append((at, context))
        print(f"[live] {agent}: {len(picked)} turns you spoke, from {thread}", file=sys.stderr)
        for at, context in reversed(picked):
            digest = hashlib.sha1(f"{thread}\n{_text(messages[at].content)}".encode()).hexdigest()[:8]
            case_id = f"live-{agent}-{digest}"
            cases[case_id] = {
                "id": case_id, "room": room, "utterance": user_words(messages[at].content),
                "expect": {"agent": agent}, "context": context,
                "history": {
                    "thread_id": thread,
                    "checkpoint": {**checkpoint, "channel_values": {**checkpoint["channel_values"],
                                                                    "messages": messages[:at]}},
                    # The keys a saver's metadata always has, all JSON.
                    "metadata": {key: value for key, value in (stored.get("metadata") or {}).items()
                                 if key in ("source", "step", "parents")},
                    "new_versions": stored.get("new_versions") or {},
                },
            }
    if only:
        missing = [case_id for case_id in only if case_id not in cases]
        if missing:
            raise ValueError(f"unknown case id(s): {missing}")
        return [cases[case_id] for case_id in only]
    return list(cases.values())


def seed_history(case: dict, db_path: str) -> None:
    """Puts a live case's conversation in the throwaway database as its
    thread's newest checkpoint, so the turn runs on the history it had; a case
    without one runs on none. Comes after `reset_history`, which clears them."""
    history = case.get("history")
    if not history:
        return
    from core.knowledge.memory.sqlite_checkpointer import SqliteCheckpointer

    config = {"configurable": {"thread_id": history["thread_id"], "checkpoint_ns": ""}}
    SqliteCheckpointer(db_path).put(config, history["checkpoint"], history["metadata"], history["new_versions"])


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
    from core.config.settings import Config
    from core.channel.voice.engines.stt import SpeechRecognizer, agent_vocabulary
    from core.channel.voice.listening.turn_taking import merge_transcripts
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
    from core.channel.discord.stream_buffer import DiscordStreamBuffer
    from core.loaders.agents_loader import AgentsLoader
    from core.runtime.foundation import events
    from core.runtime.foundation.sessions import SessionManager
    from core.runtime.observability.turn_trace import Stage, start_turn_trace
    from core.channel.voice.speech.missing_speech import fallback_speech
    from core.channel.voice.speech.reply import ERROR_LINE, speech_of
    from core.channel.voice.engines.tts import agent_voice
    from core.channel.voice.speech.pcm_speaker import PCMSpeaker
    from core.channel.voice.speech.speech_router import SpeechTextRouter
    from core.channel.voice.speech.voice_stream import VoiceStream

    reset_history(db_path)
    seed_history(case, db_path)
    BLOCKED_WRITES.clear()
    heard = await hear(case["utterance"]) if hear is not None else None
    result = CaseResult(case_id=case["id"], run=run, marks={}, room=case.get("room", ""),
                        context=case.get("context", ""))
    if hear is not None and heard is None:
        result.transcript = ""
        result.failures.append("STT heard nothing")
        return result

    started = time.time()
    trace = start_turn_trace(voice_bot, run, speech_end_at=heard.speech_end_at if heard else None, case=case["id"])
    if heard is not None:
        result.transcript = heard.transcript
        trace.mark(Stage.VAD_FINAL, at=heard.vad_final_at)
        trace.mark(Stage.STT_DONE, at=heard.stt_done_at)
    else:
        result.transcript = case["utterance"]

    session = SessionManager.get_session(
        agent_id=voice_bot, source=REPLAY_SOURCE, channel=case["room"], surface="voice", record_memory=False,
    )
    agent = AgentsLoader().get_agent(voice_bot)
    router, voice_stream = SpeechTextRouter(), VoiceStream()
    said, shown = [], []
    speaking = voice_bot  # the agent whose words are being said
    tts_task = None

    async def first_audio(text, voice):
        # Streamed into a speaker no one plays: its first audio is the mark.
        out = PCMSpeaker(asyncio.get_running_loop(), on_audio=lambda: trace.mark(Stage.TTS_FIRST_BYTE))
        stream = await tts.open(voice, out)
        stream.send(text)
        stream.finish()
        await asyncio.wait_for(out.ready(), 30)

    def speak(chunks):
        nonlocal tts_task
        for chunk in chunks:
            trace.mark(Stage.FIRST_CHUNK)
            result.spoken.append(chunk)
            if tts_task is None and measure_tts and tts is not None:
                # Synthesized beside the stream, as the voice pipeline's speech
                # lane does, in the voice of the agent speaking.
                tts_task = asyncio.create_task(first_audio(chunk, agent_voice(speaking)))

    def route(speech_part, text_part):
        # As the voice pipeline routes them: the <speech> text to the speaker,
        # everything else to the text channel.
        shown.append(text_part)
        if speech_part:
            said.append(speech_part)
            speak(voice_stream.add(speech_part))

    response = None
    trace.mark(Stage.AGENT_START)
    async for event in agent.execute_stream(result.transcript, session=session):
        kind = event.get("type")
        if kind == events.TOKEN and event.get("content"):
            result.agents = sorted(set(result.agents) | {event.get("agent_id") or voice_bot})
            if event.get("is_header") or event.get("is_error"):
                # As the voice pipeline does: a delegate's attribution and its
                # error go to the channel only.
                shown.append(event["content"])
                if event.get("is_error"):
                    result.errored = True
                    result.failures.append(f"agent error: {event['content'].strip()}")
            else:
                trace.mark(Stage.FIRST_TOKEN)
                speaking = event.get("agent_id") or speaking
                route(*router.feed(event["content"]))
        elif kind == events.TOOL_START:
            result.tools.append(event.get("tool_name", ""))
            trace.event("tool_start", tool=event.get("tool_name", ""))
        elif kind == events.TOOL_END:
            trace.event("tool_end", tool=event.get("tool_name", ""))
        elif kind == events.FINAL_RESPONSE:
            route(*router.flush())
            response = event.get("response")
            result.reply = event.get("text", "") or ""
        elif kind == events.ERROR:
            result.errored = True
            result.failures.append(f"agent error: {event.get('content')}")
    route(*router.flush())
    speak(voice_stream.flush())
    result.speech = "".join(said).strip()
    result.shown = DiscordStreamBuffer.filter_xml_for_stream("".join(shown))
    if result.errored:
        speak([ERROR_LINE])
    else:
        result.speech_missing = speech_of(response) is None
        if result.speech_missing and result.reply.strip():
            speak([line for line in [fallback_speech(result.reply)] if line])
    if tts_task is not None:
        await tts_task

    summary = trace.finish(session_id=None)
    result.marks = summary["marks"]
    result.model_calls = [e for e in summary["events"] if e["kind"] == "model_call"]
    result.model_replies = [e for e in summary["events"] if e["kind"] == "model_reply"]
    result.thinking_picks = [e for e in summary["events"] if e["kind"] == "thinking_pick"]
    result.light_events = [e for e in summary["events"] if e["kind"] in LIGHT_EVENTS]
    result.tokens = replay_tokens(started, db_path)
    result.blocked_writes = [f"{tool}.{action} {target or ''}".strip() for tool, action, target in BLOCKED_WRITES]
    result.failures += check_expectations(case, result)
    try:
        speech_failures, result.notes = check_speech(result)
    except Exception as e:
        # The checks read whatever the model wrote. A gap in them is noted on
        # this run, and must not end a benchmark that has run for hours.
        speech_failures, result.notes = [], [f"speech check skipped: {type(e).__name__}: {e}"]
    result.failures += speech_failures
    return result


# --- the benchmark's runs: combos, pacing, rate limits, resuming ---------------

async def run_combo(case: dict, run: int, combo: Combo, db_path: str, **kwargs) -> CaseResult:
    """`run_case` with every agent model call held to `combo` (`combo_scope`),
    and the light first call on the combo's light model, or off (`light_scope`).
    A run whose light call the agent's own model didn't follow fails too."""
    with combo_scope(combo), light_scope(combo.light):
        result = await run_case(case, run, db_path, **kwargs)
    result.combo = combo.label
    result.failures += combo_misses(combo, result.model_calls)
    if not result.errored:  # a run that failed stopped where it failed
        result.failures += light_misses(result.model_calls)
    return result


def rotated(combos: List[Combo], turn: int) -> List[Combo]:
    """`combos` from the `turn`-th on, wrapping around. A case's run goes on every
    combo back to back, so all of them see the same live data, and each combo
    takes the first place in turn: the first run warms what the next ones
    reuse, Gemini's cache of the agent's prompt and the tools' own caches."""
    k = turn % len(combos)
    return combos[k:] + combos[:k]


# A run the API refused for its rate limit: Gemini's 429 RESOURCE_EXHAUSTED,
# which core/util/errors.py words as "[429] You exceeded your current quota...".
RATE_LIMIT_RE = re.compile(r"\b429\b|resource.?exhausted|resource has been exhausted|too many requests|quota",
                           re.IGNORECASE)
# How long the refusal asks for: "Please retry in 38.9s.", or RetryInfo's "retryDelay": "38s".
RETRY_IN_RE = re.compile(r"retry in (\d+(?:\.\d+)?) ?s|retryDelay\W+(\d+(?:\.\d+)?)s", re.IGNORECASE)
# Seconds before each new try of a refused run. By the time a run fails, the
# SDK has already retried the call itself for ~30 s (core/runtime/model/retry.py).
RATE_LIMIT_WAITS = (30, 60, 120, 240, 300)

# Indirection so tests can skip the waits.
_sleep = asyncio.sleep
_clock = time.monotonic


def rate_limited(result: CaseResult) -> Optional[str]:
    """The error a rate-limit refusal failed `result` with, or None."""
    if not result.errored:
        return None
    return next((f for f in result.failures if f.startswith("agent error") and RATE_LIMIT_RE.search(f)), None)


def retry_wait(error: str, attempt: int) -> float:
    """Seconds to wait after refusal number `attempt` (0 for the first): the next
    of RATE_LIMIT_WAITS, or longer when the refusal asks for longer."""
    match = RETRY_IN_RE.search(error)
    asked = float(next(group for group in match.groups() if group)) if match else 0.0
    return max(asked, RATE_LIMIT_WAITS[min(attempt, len(RATE_LIMIT_WAITS) - 1)])


class Pacer:
    """Keeps the replay under `rpm` model calls a minute (0: no cap).

    A run's calls are counted from its trace, its `model_call` events, and the
    next run starts no sooner than they allow: at --rpm 12, a run that made 3
    calls takes at least 15 s, waiting out what it didn't use. Runs go one at
    a time, so that holds the rate across runs; within one, a burst is a few
    calls at most, and a refusal is waited out anyway (`run_paced`).
    """

    def __init__(self, rpm: int = 0):
        self.rpm = rpm
        self._next = 0.0  # the earliest the next run may start, on `_clock`

    async def wait(self) -> None:
        delay = self._next - _clock()
        if delay > 0:
            if delay >= 1:
                print(f"[replay] waiting {delay:.0f} s to stay under {self.rpm} model calls a minute",
                      file=sys.stderr)
            await _sleep(delay)

    def ran(self, started: float, calls: int) -> None:
        """A run that began at `started`, on `_clock`, made `calls` model calls."""
        if self.rpm > 0:
            self._next = max(self._next, started + calls * 60 / self.rpm)


async def run_paced(case: dict, run: int, combo: Combo, db_path: str, pacer: Pacer,
                    **kwargs) -> Optional[CaseResult]:
    """One run of `case` on `combo` once the pacer allows, tried again while the
    API refuses it for the rate limit. None when it still does after every
    RATE_LIMIT_WAITS: the replay stops there, and the same command resumes."""
    for attempt in range(len(RATE_LIMIT_WAITS) + 1):
        await pacer.wait()
        started = _clock()
        try:
            result = await run_combo(case, run, combo, db_path, **kwargs)
            refused, calls = rate_limited(result), len(result.model_calls)
        except Exception as e:  # a refusal the agent's own error handling didn't see: TTS, say
            if not RATE_LIMIT_RE.search(str(e)):
                raise
            result, refused, calls = None, f"{type(e).__name__}: {e}", 0
        pacer.ran(started, calls)
        if refused is None:
            return result
        if attempt < len(RATE_LIMIT_WAITS):
            wait = retry_wait(refused, attempt)
            print(f"[replay] rate-limited ({refused[:200]}); trying again in {wait:.0f} s", file=sys.stderr)
            await _sleep(wait)
    return None


# --- the service check: is now a fair time to time the models? ------------------

CHECK_PROMPT = "Reply with the single word: ready."
CHECK_CALLS = 2
# A tier is slow when every check call takes longer than this to its first
# chunk. A one-word reply at the tier's lowest thinking level normally comes
# well inside it: the NAS's short FLASH_LITE replies took 0.8 s p50 on Nov 2,
# and 10-13 s in the slow spell of Nov 3 that this check is for.
SLOW_FIRST_CHUNK_S = {"FLASH_LITE": 2.0, "FLASH": 3.0, "PRO": 6.0}
# A check call still waiting after this long is given up on. One such call, or
# one that fails, makes its tier slow by itself: on Nov 3 PRO hung on one of
# its two calls, answered the other in 3.2 s, and then hung for 109 s and 71 s
# in the run. (Model calls have no timeout of their own, so a stalled check
# call would otherwise hang the check too.)
CHECK_GIVE_UP_S = 15.0
# The exit code when it finds the service slow.
SLOW_SERVICE = 4


def tiers_to_check(combos: List[Combo]) -> List[str]:
    """The tiers a benchmark's combos run on: each one a combo forces or its
    light first call runs on, and every agent tier when a combo keeps prod's
    models. None for a plain replay."""
    if combos == [Combo()]:
        return []
    tiers = {combo.model for combo in combos if combo.model}
    tiers |= {tier_and_level(combo.light)[0] for combo in combos if combo.light}
    if any(combo.model is None for combo in combos):
        tiers |= set(TIER_ORDER)
    return [tier for tier in TIER_ORDER if tier in tiers]


async def first_chunk_s(tier: str) -> float:
    """Seconds to the first streamed chunk of a one-word reply from `tier`, at
    its lowest thinking level, so the time is the service's, not thinking's."""
    from core.config.models import resolve_model
    from core.config.settings import Config
    from core.runtime.model.retry import RetryingChatGoogleGenerativeAI

    llm = RetryingChatGoogleGenerativeAI(model=resolve_model(tier), max_retries=Config().llm_max_retries)
    started, first = _clock(), None
    async for _ in llm.astream(CHECK_PROMPT, thinking_level=TIER_THINKING_LEVELS[tier][0]):
        if first is None:
            first = _clock()
    return (first if first is not None else _clock()) - started


# Indirection so tests can answer the check's calls.
_first_chunk = first_chunk_s


async def check_service(tiers: List[str]) -> Dict[str, List[float]]:
    """Each tier's check calls, one at a time: seconds to the first chunk, or
    infinity for a call that failed or was given up on."""
    times: Dict[str, List[float]] = {}
    for tier in tiers:
        times[tier] = []
        for _ in range(CHECK_CALLS):
            try:
                times[tier].append(await asyncio.wait_for(_first_chunk(tier), CHECK_GIVE_UP_S))
            except asyncio.TimeoutError:
                print(f"[check] {tier}: no first chunk after {CHECK_GIVE_UP_S:g} s, so given up on", file=sys.stderr)
                times[tier].append(float("inf"))
            except Exception as e:
                print(f"[check] {tier}: a call failed: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
                times[tier].append(float("inf"))
    return times


def slow_tiers(times: Dict[str, List[float]]) -> List[str]:
    """The tiers whose check calls all took longer than their limit, or of which
    any one hung or failed (infinity)."""
    return [tier for tier, seconds in times.items()
            if seconds and (min(seconds) > SLOW_FIRST_CHUNK_S[tier] or float("inf") in seconds)]


def check_lines(times: Dict[str, List[float]]) -> List[str]:
    return [f"[check] {tier}: first chunk in "
            + ", ".join("none" if s == float("inf") else f"{s:.2f} s" for s in seconds)
            + f" (slow when all are over {SLOW_FIRST_CHUNK_S[tier]:g} s, or any hangs)"
            for tier, seconds in times.items()]


def load_results(path: Optional[str]) -> List[dict]:
    """The runs a results file holds, as --out writes them; none without the file."""
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path} isn't a list of runs")
    return data


def save_json(path: str, data) -> None:
    """Writes `data` to `path` whole or not at all: into a file beside it, synced,
    then renamed over it. So a replay stopped at its timeout keeps every run it
    finished, and the file is never half written."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _key(run: dict) -> Tuple[str, str, int]:
    """A stored run's (case, combo, run); one stored before combos ran on prod."""
    return run["case_id"], run.get("combo", PROD), run["run"]


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
    parser.add_argument("--model", metavar="TIER",
                        help=f"Run every agent model call on this tier: {', '.join(TIER_ORDER)}.")
    parser.add_argument("--thinking", metavar="LEVEL",
                        help=f"Run every agent model call at this thinking level: {', '.join(THINKING_LEVELS)}.")
    parser.add_argument("--combo", action="append", metavar="TIER:LEVEL",
                        help=f"Run each case on this combo (repeatable, or comma-separated), e.g. FLASH:low. "
                             f"TIER alone keeps production's thinking, {PROD}:LEVEL each agent's own model, "
                             f"{PROD} both.")
    parser.add_argument("--matrix", action="store_true",
                        help=f"Run the full matrix: {', '.join(TIER_ORDER)} at {', '.join(MATRIX_LEVELS)} "
                             f"thinking where the tier takes it, plus {PROD}.")
    parser.add_argument("--light", action="append", metavar="TIER:LEVEL",
                        help="The voice graph's light first call: run each combo with this light model, e.g. "
                             "FLASH_LITE:minimal, or off (repeatable, or comma-separated). Default: VOICE_LIGHT_MODEL.")
    parser.add_argument("--rpm", type=int, default=0,
                        help="Keep model calls under this many a minute, counted from each run's trace (0: no cap).")
    parser.add_argument("--out", help="Results JSON: read first, so the same command resumes, "
                                      "and rewritten after every run.")
    parser.add_argument("--anyway", action="store_true",
                        help="Run the combos even when the service check finds the models slow.")
    parser.add_argument("--live", metavar="NAMES",
                        help="Take the cases from these agents' conversations in production's memory.db (ids or "
                             "names, comma-separated, e.g. Ted,Aki) instead of --cases: each turn you spoke, "
                             "replayed on the conversation before it.")
    parser.add_argument("--live-turns", type=int, default=LIVE_TURNS, metavar="N",
                        help=f"How many of each conversation's latest turns --live takes (default {LIVE_TURNS}).")
    args = parser.parse_args(argv)
    try:
        if args.light:
            lights = [parse_light(text) for value in args.light for text in value.split(",") if text.strip()]
        else:
            from core.config.settings import Config

            lights = [parse_light(Config().voice_light_model)]
        combos = combos_from(args.model, args.thinking, args.combo, args.matrix, lights=lights)
    except ValueError as e:
        parser.error(str(e))
    if args.rpm < 0:
        parser.error("--rpm can't be negative")
    if args.live_turns < 1:
        parser.error("--live-turns must be at least 1")

    if args.live:
        try:  # production's memory.db, read before isolate_sessions points every store away from it
            cases = live_cases([name.strip() for name in args.live.split(",") if name.strip()], args.live_turns,
                               only=args.only)
        except ValueError as e:
            parser.error(str(e))
    else:
        cases = load_cases(args.cases, args.only, args.allow_mutations)
    if not cases:
        print("No cases to run.")
        return 1

    # Case by case and run by run, each on every combo (see `rotated`).
    case_runs = [(case, run) for case in cases for run in range(1, args.runs + 1)]
    plan = [(case, run, combo) for turn, (case, run) in enumerate(case_runs) for combo in rotated(combos, turn)]
    done = load_results(args.out)
    have = {_key(r) for r in done}
    todo = [(case, run, combo) for case, run, combo in plan if (case["id"], combo.label, run) not in have]
    if len(todo) < len(plan):
        print(f"[replay] {len(plan) - len(todo)} of the {len(plan)} runs are in {args.out} already", file=sys.stderr)

    stopped = False
    if todo:
        db_path = isolate_sessions(args.state_dir or tempfile.mkdtemp(prefix="aoc-voice-replay-"))

        from core.runtime import bootstrap
        from core.runtime.foundation.context import surface_scope
        from core.channel.voice.engines.tts import TTSEngine

        bootstrap.wire()
        tts = TTSEngine() if not args.no_tts else None
        hear = live_hearing() if args.stt == "live" else None

        if not args.allow_mutations:
            guard_writes()

        # A plain replay, with no combo or light asked for, is production as
        # configured, light first call included: a quality run, not timed.
        plain = not (args.combo or args.matrix or args.model or args.thinking or args.light)
        tiers = [] if plain else tiers_to_check(combos)
        if tiers:
            times = await check_service(tiers)
            for line in check_lines(times):
                print(line, file=sys.stderr)
            slow = slow_tiers(times)
            if slow and not args.anyway:
                print(f"[check] The model service is slow right now ({', '.join(slow)}), so the combos' latency "
                      f"would measure that, not them. Nothing was run: try again later, or add --anyway.",
                      file=sys.stderr)
                return SLOW_SERVICE
            if slow:
                print(f"[check] Slow ({', '.join(slow)}), but running anyway, as asked.", file=sys.stderr)

        pacer = Pacer(args.rpm)
        with surface_scope("voice"), without_pruning():
            for case, run, combo in todo:
                print(f"[replay] {case['id']} run {run} on {combo.label}: {case['utterance']!r}", file=sys.stderr)
                result = await run_paced(case, run, combo, db_path, pacer, voice_bot=args.voice_bot, hear=hear,
                                         tts=tts, measure_tts=not args.no_tts)
                if result is None:
                    print("[replay] Still rate-limited, so stopping here: run the same command again to resume.",
                          file=sys.stderr)
                    stopped = True
                    break
                done.append({**asdict(result), "passed": result.passed})
                if args.out:
                    save_json(args.out, done)

    order = {(case["id"], combo.label, run): i for i, (case, run, combo) in enumerate(plan)}
    results = sorted((CaseResult.from_dict(r) for r in done if _key(r) in order),
                     key=lambda r: order[(r.case_id, r.combo, r.run)])
    print(render_combos(results, [combo.label for combo in combos]))
    if stopped:
        return 3
    return 0 if all(r.passed for r in results) else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
