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
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Set, Tuple

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

    Taken from each tool's own read bundle, or from the `READ_ACTIONS` every
    tool that can write declares, so a new read action or a new write tool
    needs no change here. Tools that declare neither (yfinance, seats_aero,
    zillow_query, ...) have no write actions to guard.
    """
    import importlib

    from tools import filesystem, home_assistant

    allowed = {
        "filesystem": set(filesystem.PERMISSION_BUNDLES["@read"]),
        "home_assistant": set(home_assistant.PERMISSION_BUNDLES["@observe"]),
        "bash": set(),
    }
    tools_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
    for filename in sorted(os.listdir(tools_dir)):
        tool = filename[:-3]
        if not filename.endswith(".py") or filename.startswith("_") or tool in allowed:
            continue
        with open(os.path.join(tools_dir, filename), encoding="utf-8") as f:
            declares = re.search(r"^READ_ACTIONS\b", f.read(), re.MULTILINE)
        if declares:  # import only the tools that declare it
            allowed[tool] = set(importlib.import_module(f"tools.{tool}").READ_ACTIONS)
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
    thinking_picks: List[dict] = field(default_factory=list)  # set_thinking: level, applied, reason
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
        hour, _, minutes = token.partition(":")
        forms = {str(int(hour)), str(int(hour) % 12 or 12)}
        return forms | ({str(int(minutes))} if minutes.isdigit() and int(minutes) else set())
    token = token.replace(",", "")
    try:
        return {_number(float(token))}
    except ValueError:
        return set()


def _required_numbers(reply: str) -> List[Tuple[str, List[Set[str]]]]:
    """(label, alternatives): each number of the reply that speech must keep, with
    the sets of spoken forms that count as keeping it."""
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
            hour, _, minutes = token.partition(":")
            alternatives = [{str(int(hour))}, {str(int(hour) % 12 or 12)}]
            if minutes.isdigit() and int(minutes):
                alternatives = [a | {str(int(minutes))} for a in alternatives]
            required.append((token, alternatives))
        elif "." in token and money:  # $3.50: dollars and cents
            dollars, _, cents = token.partition(".")
            forms = {str(int(dollars))} | ({str(int(cents))} if int(cents) else set())
            required.append((f"${token}", [{_number(float(token))}, forms]))
        elif "." in token:
            whole, _, fraction = token.partition(".")
            required.append((token, [{_number(float(token))}, {str(int(whole)), str(int(fraction))}]))
        else:
            required.append(((money or "") + token, [{_number(float(token.replace(",", "")))}]))
    return required


def _words(text: str) -> List[str]:
    """`text` as lowercase words, possessives dropped."""
    return re.findall(r"[a-z0-9]+", text.lower().replace("’", "'").replace("'s", ""))


def _required_names(reply: str) -> List[str]:
    """Named items of the reply: bold phrases, runs of capitalized words
    ("Point Reyes", "Five Guys"), and single capitalized words in list items
    ("Sarah", "Goldbelly").

    Not counted: headings; field labels, which are short or repeat on every
    item ("**Due Date**:") -- a longer one-off label is the item's own name;
    and a single capitalized word that opens a sentence or phrase, or sits in
    running prose, as often a greeting ("Good morning Alva") or a stressed
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
        lines.append(
            f"| {r.case_id} | {r.run} | {'PASS' if r.passed else 'FAIL'} "
            f"| {_ms(r.marks.get('first_token'))} | {_ms(r.marks.get('first_chunk'))} | {_ms(r.marks.get('tts_first_byte'))} "
            f"| {len(r.model_calls)} ({calls}) | {r.tokens.get('input', 0)}/{r.tokens.get('output', 0)} "
            f"| {'; '.join(r.failures + r.notes + [f'refused write: {w}' for w in r.blocked_writes] + _picks(r))} |"
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
    return "\n".join(lines)


def _picks(r: CaseResult) -> List[str]:
    """The agent's set_thinking picks, with the reason it gave."""
    return [f"picked {p['applied']}" + (f" (asked {p['level']})" if p["level"] != p["applied"] else "")
            + f": {p['reason']}" for p in r.thinking_picks]


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
    from core.voice.listening.turn_taking import merge_transcripts
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
    from core.runtime.session_manager import SessionManager
    from core.runtime.turn_trace import start_turn_trace
    from core.voice.speech.missing_speech import fallback_speech
    from core.voice.speech.reply import ERROR_LINE, speech_of
    from core.voice.engines.tts import agent_voice
    from core.voice.speech.pcm_speaker import PCMSpeaker
    from core.voice.speech.speech_router import SpeechTextRouter
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
    router, voice_stream = SpeechTextRouter(), VoiceStream()
    said, shown = [], []
    speaking = voice_bot  # the agent whose words are being said
    tts_task = None

    async def first_audio(text, voice):
        # Streamed into a speaker no one plays: its first audio is the mark.
        out = PCMSpeaker(asyncio.get_running_loop(), on_audio=lambda: trace.mark("tts_first_byte"))
        stream = await tts.open(voice, out)
        stream.send(text)
        stream.finish()
        await asyncio.wait_for(out.ready(), 30)

    def speak(chunks):
        nonlocal tts_task
        for chunk in chunks:
            trace.mark("first_chunk")
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
    trace.mark("agent_start")
    async for event in agent.execute_stream(result.transcript, session=session):
        kind = event.get("type")
        if kind == "token" and event.get("content"):
            result.agents = sorted(set(result.agents) | {event.get("agent_id") or voice_bot})
            if event.get("is_header") or event.get("is_error"):
                # As the voice pipeline does: a delegate's attribution and its
                # error go to the channel only.
                shown.append(event["content"])
                if event.get("is_error"):
                    result.errored = True
                    result.failures.append(f"agent error: {event['content'].strip()}")
            else:
                trace.mark("first_token")
                speaking = event.get("agent_id") or speaking
                route(*router.feed(event["content"]))
        elif kind == "tool_start":
            result.tools.append(event.get("tool_name", ""))
            trace.event("tool_start", tool=event.get("tool_name", ""))
        elif kind == "tool_end":
            trace.event("tool_end", tool=event.get("tool_name", ""))
        elif kind == "final_response":
            route(*router.flush())
            response = event.get("response")
            result.reply = event.get("text", "") or ""
        elif kind == "error":
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
    result.thinking_picks = [e for e in summary["events"] if e["kind"] == "thinking_pick"]
    result.tokens = replay_tokens(started, db_path)
    result.blocked_writes = [f"{tool}.{action} {target or ''}".strip() for tool, action, target in BLOCKED_WRITES]
    result.failures += check_expectations(case, result)
    speech_failures, result.notes = check_speech(result)
    result.failures += speech_failures
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
    from core.voice.engines.tts import TTSEngine

    tts = TTSEngine() if not args.no_tts else None
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
